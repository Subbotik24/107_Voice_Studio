import unittest

from hermes_whisper.metrics import (
    ErrorRateAccumulator,
    character_error_rate,
    edit_distance,
    word_error_rate,
)


class MetricTests(unittest.TestCase):
    def test_edit_distance(self) -> None:
        self.assertEqual(edit_distance(list("kitten"), list("sitting")), 3)

    def test_error_rates(self) -> None:
        self.assertEqual(word_error_rate("Один два", "один два"), 0.0)
        self.assertEqual(word_error_rate("один два", "один"), 0.5)
        self.assertGreater(character_error_rate("český", "cesky"), 0.0)

    def test_corpus_accumulator(self) -> None:
        accumulator = ErrorRateAccumulator()
        accumulator.update("один два", "один")
        accumulator.update("tři", "tři")
        result = accumulator.as_dict()
        self.assertEqual(result["utterances"], 2)
        self.assertEqual(result["reference_words"], 3)
        self.assertAlmostEqual(result["wer"], 1 / 3)


if __name__ == "__main__":
    unittest.main()
