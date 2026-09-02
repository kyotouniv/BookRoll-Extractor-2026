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


def encrypted_page(payload: bytes) -> str:
    iv = "123456"
    inner = base64.b64encode(payload)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(inner) + padder.finalize()
    key = ("uc5xi" + iv + "ndw2j").encode("utf-8")
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return json.dumps({"iv": iv, "data": f"Hah6lu3wie('{base64.b64encode(ciphertext).decode('ascii')}')"})


class FakeBookRollHandler(BaseHTTPRequestHandler):
    token = "T" * 128
    page_pdf: bytes = b""

    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, body: bytes, status: int = 200, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/bookroll/book/view":
            self.send_response(302)
            self.send_header("Location", f"/bookroll/book/view/{self.token}")
            self.end_headers()
        elif path == "/bookroll/v1/contents/demo":
            self._send(json.dumps({"contents": {"title": "Demo", "pages": 1, "version": 2, "pdf": True}}).encode())
        elif path == "/bookroll/v1/contents/demo/image/1":
            self._send(encrypted_page(self.page_pdf).encode())
        else:
            self._send(b"not found", 404, "text/plain")


class LocalExtractionTests(unittest.TestCase):
    def test_extract_material_against_local_protocol_fixture(self) -> None:
        source = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(source)
        FakeBookRollHandler.page_pdf = source.getvalue()
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
                result = extract_material(
                    plan,
                    cookie="SESSION=test",
                    base_url=f"http://127.0.0.1:{server.server_address[1]}/bookroll",
                    delay=0,
                )
                self.assertEqual(result["pages"], 1)
                self.assertEqual(len(PdfReader(str(plan.output_pdf)).pages), 1)
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
