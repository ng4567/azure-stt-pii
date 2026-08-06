import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend.app import main, uploads


class UploadGroundTruthTests(unittest.TestCase):
    def test_create_stores_annotation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = root / "transcript.txt"
            annotations = root / "annotations.json"
            transcript.write_text("REP: Call Maya.", encoding="utf-8")
            annotations.write_text(
                json.dumps({"entities": [{"category": "PERSON", "text": "Maya", "offset": 5, "length": 4}]}),
                encoding="utf-8",
            )
            with patch.object(uploads, "UPLOAD_DIR", root / "uploads"):
                meta = uploads.create(
                    None,
                    None,
                    transcript,
                    transcript.name,
                    pii_ground_truth_source=annotations,
                    pii_ground_truth_filename=annotations.name,
                )
                self.assertEqual(meta["pii_ground_truth"]["entities"], 1)
                self.assertTrue(uploads.pii_ground_truth_path(meta["id"]).is_file())

    def test_api_requires_reference_for_annotations(self) -> None:
        upload = unittest.mock.Mock(filename="annotations.json")
        with self.assertRaises(HTTPException) as context:
            main.create_upload(audio=None, transcript=None, pii_ground_truth=upload)
        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
