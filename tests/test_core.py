from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from bookroll_automation.core import build_plan, decrypt_page, parse_home_html


class CoreTests(unittest.TestCase):
    def test_parse_home_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home.html"
            home.write_text(
                '<a href="/bookroll/book/view?contents=abc123abc123abc123abc123abc123ab"> A &amp; B </a>'
                '<a href="/bookroll/book/view?contents=def456def456def456def456def456de">Second</a>',
                encoding="utf-8",
            )
            materials = parse_home_html(home)
            self.assertEqual([material.title for material in materials], ["A & B", "Second"])
            plans = build_plan(home, root / "output", selection="1")
            self.assertEqual(len(plans), 1)
            self.assertIn("01_A & B_bookroll.pdf", str(plans[0].output_pdf))

    def test_decrypt_page(self) -> None:
        iv = "123456"
        payload = b"%PDF-1.7 synthetic-test"
        payload_b64 = base64.b64encode(payload)
        padder = padding.PKCS7(128).padder()
        padded = padder.update(payload_b64) + padder.finalize()
        key = ("uc5xi" + iv + "ndw2j").encode("utf-8")
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        encrypted = base64.b64encode(ciphertext).decode("ascii")
        response = {"iv": iv, "data": f"Hah6lu3wie('{encrypted}')"}
        self.assertEqual(decrypt_page(response), payload)


if __name__ == "__main__":
    unittest.main()
