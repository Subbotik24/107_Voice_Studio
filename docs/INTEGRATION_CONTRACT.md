# Контракт інтеграції STT у Hermes Voice Studio

## Розмежування

- `hermes_voice_studio` відповідає за UI, мікрофон, файли, словники, історію, retention та export.
- `hermes_whisper` відповідає за model architecture, training, inference і `.hws`.
- `faster-whisper` є зовнішнім локальним production adapter.

Desktop‑шар працює тільки з `SpeechEngine.transcribe()` та `EngineResult`.

## Вхід

```json
{
  "source": "/absolute/path/audio.wav",
  "language": "auto"
}
```

## Вихід

```json
{
  "engine": "hermes-whisper",
  "model": "hermes-whisper-150m",
  "language": "uk",
  "audio_seconds": 12.4,
  "elapsed_seconds": 5.1,
  "segments": [
    {
      "start": 0.0,
      "end": 12.4,
      "text": "...",
      "language": "uk",
      "confidence": 0.0
    }
  ],
  "metadata": {}
}
```

`raw_text` формується з engine segments. `corrected_text` і segment corrections створює application dictionary layer.

## Умови Hermes activation

- вибраний `.hws` існує;
- exact member set і SHA‑256 пройшли перевірку;
- встановлено optional dependency group `hermes`;
- language — `uk`, `cs` або `auto`;
- ваги навчені та мають окремий documented benchmark.

Наявність технічно валідного `.hws` не доводить якість розпізнавання.
