import tempfile
import unittest
from pathlib import Path

from hermes_whisper.tokenizer import HermesTokenizer, TimestampedText, normalize_text


class TokenizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.samples = [
            "Український технічний опис — № 17.",
            "Český technický popis: příliš žluťoučký kůň.",
            "Hermes AI: 12,5 м² / 12,5 m².",
        ]
        self.tokenizer = HermesTokenizer.train(
            self.samples,
            target_text_vocab_size=340,
            min_pair_frequency=1,
            languages=("uk", "cs"),
            timestamp_resolution=0.1,
            max_timestamp_seconds=5.0,
        )

    def test_lossless_round_trip(self) -> None:
        for sample in self.samples:
            with self.subTest(sample=sample):
                token_ids = self.tokenizer.encode_text(sample)
                self.assertEqual(self.tokenizer.decode_text(token_ids), normalize_text(sample))

    def test_persistence_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer.json"
            self.tokenizer.save(path)
            first = path.read_bytes()
            loaded = HermesTokenizer.load(path)
            loaded.save(path)
            second = path.read_bytes()
        self.assertEqual(first, second)
        self.assertEqual(loaded.vocab_size, self.tokenizer.vocab_size)

    def test_timestamped_transcript(self) -> None:
        sequence = self.tokenizer.encode_transcript(
            "ignored aggregate",
            language="uk",
            segments=(
                TimestampedText(0.0, 1.2, "Перший."),
                TimestampedText(1.2, 2.4, "Другий."),
            ),
        )
        self.assertEqual(sequence[0], self.tokenizer.bos_id)
        self.assertEqual(sequence[1], self.tokenizer.language_id("uk"))
        self.assertEqual(sequence[-1], self.tokenizer.eos_id)
        timestamps = [
            self.tokenizer.timestamp_seconds(token_id)
            for token_id in sequence
            if self.tokenizer.timestamp_seconds(token_id) is not None
        ]
        self.assertEqual(timestamps, [0.0, 1.2, 1.2, 2.4])

    def test_training_is_deterministic(self) -> None:
        second = HermesTokenizer.train(
            self.samples,
            target_text_vocab_size=340,
            min_pair_frequency=1,
            languages=("uk", "cs"),
            timestamp_resolution=0.1,
            max_timestamp_seconds=5.0,
        )
        self.assertEqual(second.token_bytes, self.tokenizer.token_bytes)
        self.assertEqual(second.merges, self.tokenizer.merges)


if __name__ == "__main__":
    unittest.main()
