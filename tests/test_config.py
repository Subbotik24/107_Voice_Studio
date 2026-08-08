import json
import tempfile
import unittest
from pathlib import Path

from hermes_whisper.config import ExperimentConfig, ModelConfig


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_fingerprint(self) -> None:
        config = ExperimentConfig()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config.save(path)
            loaded = ExperimentConfig.load(path)
        self.assertEqual(config, loaded)
        self.assertEqual(config.fingerprint(), loaded.fingerprint())

    def test_invalid_attention_width(self) -> None:
        config = ExperimentConfig(model=ModelConfig(d_model=257, attention_heads=8))
        with self.assertRaisesRegex(ValueError, "divisible"):
            config.validate()

    def test_repository_configs_are_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for path in (root / "configs").glob("*.json"):
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                loaded = ExperimentConfig.from_dict(payload)
                self.assertGreater(loaded.model.vocab_size, 0)


if __name__ == "__main__":
    unittest.main()
