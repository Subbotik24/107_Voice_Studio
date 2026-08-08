import tempfile
import unittest
from pathlib import Path

from hermes_whisper.manifest import (
    ManifestRecord,
    Provenance,
    load_manifest,
    manifest_fingerprint,
    summarize_manifest,
    write_manifest,
)


class ManifestTests(unittest.TestCase):
    def test_round_trip_summary_and_fingerprint(self) -> None:
        record = ManifestRecord(
            audio="/tmp/example.wav",
            text="Технічний опис.",
            language="uk",
            duration_seconds=1.5,
            provenance=Provenance(
                source="test",
                license="CC0-1.0",
                consent=True,
                speaker_id="speaker-a",
            ),
            record_id="record-1",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.jsonl"
            write_manifest([record], path)
            loaded = load_manifest(path, require_audio_exists=False)
        self.assertEqual(loaded[0].text, record.text)
        self.assertEqual(summarize_manifest(loaded)["records"], 1)
        self.assertEqual(manifest_fingerprint(loaded), manifest_fingerprint(loaded))

    def test_rejects_missing_consent(self) -> None:
        record = ManifestRecord(
            audio="/tmp/example.wav",
            text="test",
            language="cs",
            duration_seconds=1.0,
            provenance=Provenance(source="test", license="private", consent=False),
        )
        with self.assertRaisesRegex(ValueError, "not approved"):
            record.validate()


if __name__ == "__main__":
    unittest.main()
