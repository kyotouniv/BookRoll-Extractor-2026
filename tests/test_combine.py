from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from bookroll_automation.core import combine_collection


class CombineTests(unittest.TestCase):
    def test_combine_writes_pages_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.pdf"
            second = root / "second.pdf"
            for path in (first, second):
                writer = PdfWriter()
                writer.add_blank_page(width=100, height=100)
                with path.open("wb") as stream:
                    writer.write(stream)
            index = root / "collection_index.json"
            index.write_text(
                json.dumps(
                    {
                        "items": [
                            {"number": 1, "title": "First", "output_pdf": str(first)},
                            {"number": 2, "title": "Second", "output_pdf": str(second)},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "all.pdf"
            manifest = combine_collection(index, output)
            self.assertEqual(manifest["total_pages"], 2)
            self.assertEqual(len(PdfReader(str(output)).pages), 2)
            self.assertTrue((root / "combined_manifest.json").is_file())

    def test_existing_manifest_refuses_before_writing_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as stream:
                writer.write(stream)
            index = root / "collection_index.json"
            index.write_text(json.dumps({"items": [{"title": "Source", "output_pdf": str(source)}]}), encoding="utf-8")
            (root / "combined_manifest.json").write_text("{}", encoding="utf-8")
            output = root / "arbitrary" / "all.pdf"
            with self.assertRaises(FileExistsError):
                combine_collection(index, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
