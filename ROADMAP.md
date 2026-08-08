# Roadmap

## P0 — довести desktop‑застосунок до release candidate

1. **Реальний E2E на macOS та Windows**
   - мікрофон, hotkey, WAV/MP3/M4A/MP4;
   - uk/cs/en/auto;
   - keep/delete retention;
   - export усіх форматів;
   - критерій: 0 crash у контрольній серії не менше 50 задач на ОС.
   - стан: macOS 50-task PASS із task-level JSON і незмінними originals;
     manual Accessibility delivery та Windows 50-task/permissions TODO.

2. **Керування довгими операціями**
   - cancellation token;
   - progress/status для завантаження моделі та транскрибування;
   - timeout і коректне відновлення UI;
   - критерій: користувач може скасувати задачу без завершення програми.
   - стан: реалізовано persistent spawn worker, Cancel, timeout і progress.

3. **Model management**
   - локальний каталог моделей;
   - явний download/install/remove;
   - offline‑only режим;
   - перевірка вільного місця та зрозумілі помилки.
   - стан: реалізовано catalog/install/import/verify/remove/offline.

4. **Packaging**
   - PyInstaller/Nuitka pipeline;
   - `.app/.dmg` для Apple Silicon та `.exe/.msi` для Windows x64;
   - code signing/notarization;
   - automated clean‑machine install test.
   - стан: source packaging pipeline та unsigned `0.3.0-test-rc1` release
     contracts підготовлено; Windows x64 atomic build/runtime-probe/ZIP pipeline
     готовий, але фактичні 0.3.0 `.exe/.zip`, macOS `.dmg`, clean-machine
     acceptance і signing/notarization ще TODO. Не вважати попередній 0.2
     artifact підтвердженням нового RC.

5. **Storage migrations та backup**
   - versioned schema;
   - export/import backup;
   - recovery test після пошкодження DB.
   - стан: schema, audit, backup verify та reversible restore реалізовано.

6. **Benchmark**
   - фіксований набір uk/cs/en записів;
   - WER/CER/RTF/RAM/model load time;
   - профілі tiny/base/small/medium/large-v3;
   - окремі результати для Mac mini M4 і Windows target.
   - стан: harness і synthetic smoke готові; licensed corpus та production results відсутні.

## P0 — власна Hermes модель, окремий трек

Цей трек не повинен блокувати release застосунку на `faster-whisper`.

1. ліцензований corpus + provenance;
2. tokenizer corpus і manifest validation;
3. nano pilot;
4. закритий test set;
5. WER/CER/RTF release gate;
6. лише після цього — distributable `.hws`;
7. benchmark PyTorch vs ONNX/CoreML/інші runtime;
8. обраний формат квантизації та memory budget.

## P1

- speaker diarization;
- word timestamps;
- segment editor із синхронізацією subtitle text;
- batch transcription;
- profiles словників за проєктами;
- автоматичне вставлення в active app після окремого security review;
- локальний REST/IPC adapter для Hermes AI.

## P2

- system audio capture;
- live streaming encoder;
- model update channel із підписаним manifest;
- team export/import без cloud dependency.
