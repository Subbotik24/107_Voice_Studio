import hashlib
import tempfile
import unittest
from pathlib import Path

from hermes_whisper.decoding import merge_text_overlap
from hermes_whisper.tokenizer import HermesTokenizer


class BundleAndDecodingTests(unittest.TestCase):
    def test_overlap_merge(self) -> None:
        merged = merge_text_overlap(
            "це технічний опис проєкту",
            "опис проєкту для замовника",
        )
        self.assertEqual(merged, "це технічний опис проєкту для замовника")

    def test_tokenizer_file_is_stable_binary_input(self) -> None:
        tokenizer = HermesTokenizer.byte_level(
            languages=("uk", "cs"),
            timestamp_resolution=0.1,
            max_timestamp_seconds=1.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer.json"
            tokenizer.save(path)
            first = hashlib.sha256(path.read_bytes()).hexdigest()
            HermesTokenizer.load(path).save(path)
            second = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
