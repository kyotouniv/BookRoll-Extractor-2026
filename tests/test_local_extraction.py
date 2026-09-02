from __future__ import annotations

import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pypdf import PdfReader, PdfWriter

from bookroll_automation.core import MaterialPlan, extract_material
from bookroll_automation.protocol import LEGACY_ADAPTER, ProtocolAdapter, save_cached_adapter


CURRENT_ADAPTER = ProtocolAdapter(
    wrapper_name="FreshWrapper",
    key_prefix="preAA",
    image_query_key="queryX",
    key_suffix="tailZ",
)


def encrypted_page(payload: bytes, adapter: ProtocolAdapter = CURRENT_ADAPTER) -> str:
    iv = "123456"
    inner = base64.b64encode(payload)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(inner) + padder.finalize()
    key = (adapter.key_prefix + iv + adapter.key_suffix).encode("utf-8")
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return json.dumps({"iv": iv, "data": f"{adapter.wrapper_name}('{base64.b64encode(ciphertext).decode('ascii')}')"})


class FakeBookRollHandler(BaseHTTPRequestHandler):
    token = "T" * 128
    page_pdf: bytes = b""
    adapter = CURRENT_ADAPTER
    protocol_requests = 0

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, body: bytes, status: int = 200, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/bookroll/book/view":
            self.send_response(302)
            self.send_header("Location", f"/bookroll/book/view/{self.token}")
            self.end_headers()
        elif path == f"/bookroll/book/view/{self.token}":
            self._send(b'<script src="/bookroll/vue/js/app.test.js"></script>', content_type="text/html")
        elif path == "/bookroll/vue/js/app.test.js":
            type(self).protocol_requests += 1
            self._send(
                b'''getBookImage",value:function(t,e){var r="preAA",o="tailZ",q="queryX";i="".concat(api,"/contents/").concat(t,"/image/").concat(e,"?").concat(q);s=new Function("FreshWrapper",c.data.data);s((function(t){var e=xo["enc"].Utf8.parse(r+c.data.iv+o),n=xo["AES"].decrypt(t,e,{mode:xo["mode"].ECB});}));}''',
                content_type="application/javascript",
            )
        elif path == "/bookroll/v1/contents/demo":
            self._send(json.dumps({"contents": {"title": "Demo", "pages": 1, "version": 2, "pdf": True}}).encode())
        elif path == "/bookroll/v1/contents/demo/image/1":
            if parsed.query != self.adapter.image_query_key:
                self._send(b"wrong protocol query", 400, "text/plain")
            else:
                self._send(encrypted_page(self.page_pdf, self.adapter).encode())
        else:
            self._send(b"not found", 404, "text/plain")


class LocalExtractionTests(unittest.TestCase):
    def test_extract_material_against_local_protocol_fixture(self) -> None:
        source = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(source)
        FakeBookRollHandler.page_pdf = source.getvalue()
        FakeBookRollHandler.protocol_requests = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBookRollHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.subTest("extract"):
                root = Path(self._temporaryDirectory())
                plan = MaterialPlan(
                    number=1,
                    title="Demo",
                    contents="demo",
                    output_dir=root / "01_Demo",
                    output_pdf=root / "01_Demo" / "output" / "pdf" / "01_Demo_bookroll.pdf",
                )
                base_url = f"http://127.0.0.1:{server.server_address[1]}/bookroll"
                cache_dir = root / "protocol-cache"
                # A remembered, older adapter must be replaced automatically when the
                # page endpoint rejects its old query marker.
                save_cached_adapter(base_url, LEGACY_ADAPTER, cache_dir)
                result = extract_material(
                    plan,
                    cookie="SESSION=test",
                    base_url=base_url,
                    delay=0,
                    protocol_cache_dir=cache_dir,
                )
                self.assertEqual(result["pages"], 1)
                self.assertEqual(len(PdfReader(str(plan.output_pdf)).pages), 1)
                self.assertGreaterEqual(FakeBookRollHandler.protocol_requests, 1)
                self.assertTrue(any(cache_dir.glob("*.bak-v1-*")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def _temporaryDirectory(self) -> str:
        import tempfile

        if not hasattr(self, "_tmp"):  # keep the path alive through assertions
            self._tmp = tempfile.TemporaryDirectory()
        return self._tmp.name

    def tearDown(self) -> None:
        temporary = getattr(self, "_tmp", None)
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
