import importlib.util
import unittest

TORCH_AVAILABLE = importlib.util.find_spec("torch") is not None

if TORCH_AVAILABLE:
    import pytest
    import torch

    from hermes_whisper.config import AudioConfig, ModelConfig
    from hermes_whisper.data import TrainingBatch
    from hermes_whisper.losses import compute_multitask_loss
    from hermes_whisper.model import HermesSpeechModel
    from hermes_whisper.tokenizer import HermesTokenizer
    from hermes_whisper.trainer import choose_training_device


@unittest.skipUnless(TORCH_AVAILABLE, "PyTorch is not installed")
class ModelTests(unittest.TestCase):
    def test_mps_training_has_an_actionable_error(self) -> None:
        with pytest.raises(RuntimeError, match="CTC loss"):
            choose_training_device(0, "mps")

    def test_forward_loss_and_backward(self) -> None:
        tokenizer = HermesTokenizer.byte_level(
            languages=("uk", "cs"),
            timestamp_resolution=0.1,
            max_timestamp_seconds=1.0,
        )
        audio = AudioConfig(max_audio_seconds=1.0)
        config = ModelConfig(
            name="unit",
            vocab_size=tokenizer.vocab_size,
            d_model=32,
            encoder_layers=1,
            decoder_layers=1,
            attention_heads=4,
            ffn_multiplier=2.0,
            convolution_kernel=7,
            dropout=0.0,
            max_text_tokens=32,
            languages=("uk", "cs"),
            label_smoothing=0.0,
        )
        model = HermesSpeechModel(audio, config, pad_id=tokenizer.pad_id)
        self.assertEqual(
            model.parameter_count,
            config.estimated_parameter_count(audio.n_mels),
        )
        mel = torch.randn(2, audio.n_mels, 32)
        mel_lengths = torch.tensor([32, 28])
        sequences = [
            tokenizer.encode_transcript("тест", language="uk"),
            tokenizer.encode_transcript("test", language="cs"),
        ]
        maximum = max(map(len, sequences))
        padded = torch.full((2, maximum), tokenizer.pad_id, dtype=torch.long)
        for index, sequence in enumerate(sequences):
            padded[index, : len(sequence)] = torch.tensor(sequence)
        ctc_sequences = [
            tokenizer.encode_text("тест"),
            tokenizer.encode_text("test"),
        ]
        ctc_maximum = max(map(len, ctc_sequences))
        ctc = torch.full((2, ctc_maximum), tokenizer.pad_id, dtype=torch.long)
        for index, sequence in enumerate(ctc_sequences):
            ctc[index, : len(sequence)] = torch.tensor(sequence)
        batch = TrainingBatch(
            waveforms=torch.zeros(2, 1),
            sample_lengths=torch.ones(2, dtype=torch.long),
            mel_lengths=mel_lengths,
            decoder_input_ids=padded[:, :-1],
            decoder_labels=padded[:, 1:],
            ctc_targets=ctc,
            ctc_target_lengths=torch.tensor([len(item) for item in ctc_sequences]),
            language_targets=torch.tensor([0, 1]),
            texts=["тест", "test"],
            record_ids=["uk", "cs"],
        )
        output = model(mel, mel_lengths, batch.decoder_input_ids)
        self.assertEqual(output.logits.shape[:2], batch.decoder_input_ids.shape)
        self.assertEqual(output.language_logits.shape, (2, 2))
        loss = compute_multitask_loss(
            output,
            batch,
            config=config,
            pad_id=tokenizer.pad_id,
        )
        self.assertTrue(torch.isfinite(loss.total))
        loss.total.backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))


if __name__ == "__main__":
    unittest.main()
