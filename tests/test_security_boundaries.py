from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bookroll_automation.core import BookRollClient
from bookroll_automation.webui import BookRollHTTPServer, Handler, _is_loopback_host, run_server


class ExternalRedirectHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Location", "https://external.example.invalid/view/token")
        self.end_headers()


class SecurityBoundaryTests(unittest.TestCase):
    def test_external_view_redirect_is_rejected_before_a_follow_up_request(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ExternalRedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = BookRollClient("SESSION=test", f"http://127.0.0.1:{server.server_address[1]}/bookroll")
            with self.assertRaisesRegex(RuntimeError, "left the configured origin"):
                client.get_view_token("a" * 32)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_webui_rejects_cross_site_post_and_keeps_dry_run_local(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home.html"
            home.write_text(
                '<a href="/bookroll/book/view?contents=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">Demo</a>',
                encoding="utf-8",
            )
            server = BookRollHTTPServer(("127.0.0.1", 0), Handler, "127.0.0.1")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root_url = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(root_url + "/") as response:
                    page = response.read().decode("utf-8")
                token_match = re.search(r"const csrfToken = '([^']+)'", page)
                self.assertIsNotNone(token_match)
                token = token_match.group(1)
                body = urlencode(
                    {
                        "base_url": "https://private.example.invalid/bookroll",
                        "home_html": str(home),
                        "batch_dir": str(root / "output"),
                        "dry_run": "on",
                    }
                ).encode("utf-8")
                rejected = Request(root_url + "/api/jobs", data=body, method="POST")
                rejected.add_header("Content-Type", "application/x-www-form-urlencoded")
                with self.assertRaises(HTTPError) as error:
                    urlopen(rejected)
                self.assertEqual(error.exception.code, 403)
                self.assertFalse((root / "output").exists())

                accepted = Request(root_url + "/api/jobs", data=body, method="POST")
                accepted.add_header("Content-Type", "application/x-www-form-urlencoded")
                accepted.add_header("Origin", server.expected_origin)
                accepted.add_header("X-BookRoll-CSRF", token)
                with urlopen(accepted) as response:
                    job_id = json.loads(response.read().decode("utf-8"))["id"]
                with urlopen(root_url + f"/api/jobs/{job_id}") as response:
                    job = json.loads(response.read().decode("utf-8"))
                self.assertEqual(job["status"], "completed")
                self.assertTrue(job["result"]["dry_run"])
                self.assertNotIn("private.example.invalid", json.dumps(job))
                self.assertFalse((root / "output").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_webui_allows_only_loopback_bindings(self) -> None:
        self.assertTrue(_is_loopback_host("127.0.0.1"))
        self.assertTrue(_is_loopback_host("localhost"))
        self.assertFalse(_is_loopback_host("0.0.0.0"))
        with self.assertRaisesRegex(ValueError, "loopback"):
            run_server("0.0.0.0", 51837)


if __name__ == "__main__":
    unittest.main()
