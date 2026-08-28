# Архітектура VOICE Studio

## 1. Межа системи

`VOICE Studio` — одна desktop‑програма з local-first STT та одним
явно opt-in cloud adapter.

```text
Microphone / media file
        │
        ▼
TranscriptionService
        │
        ├── EngineManager ── faster-whisper (default, local)
        │                 └─ openai-cloud (explicit consent only)
        │
        ├── TerminologyDictionary
        ├── LocalStore / SQLite
        └── TXT / MD / JSON / SRT / VTT
```

UI та storage не залежать від внутрішньої архітектури моделі. Обидва движки повертають один `EngineResult`.

## 2. Пакети

| Пакет | Відповідальність |
|---|---|
| `voice_studio.app` | Tkinter GUI, worker thread, hotkey, UX |
| `voice_studio.cli` | CLI для транскрипції, моделей, backup і diagnostics |
| `voice_studio.engines` | Adapter contract, local runtime cache і explicit cloud adapter |
| `voice_studio.service` | Оркестрація транскрибування |
| `voice_studio.storage` | SHA‑256, SQLite, retention |
| `voice_studio.dictionary` | Детерміновані термінологічні заміни |
| `voice_studio.exporters` | Формати експорту |

## 3. Engine contract

Вхід:

```python
transcribe(source: Path, language: str | None) -> EngineResult
```

Вихід містить:

- `engine`, `model`, `language`;
- сегменти `start/end/text/language/confidence`;
- тривалість аудіо;
- elapsed time і RTF;
- engine‑specific metadata.

`corrected_text` не є raw output моделлю. Це окремий редагований шар програми.
OpenAI AI cleanup може запропонувати правки лише після explicit user action;
він не змінює `raw_text` і не надсилає його в cloud.

## 3.1 Cloud boundary

`openai-cloud` не є fallback для local engines. GUI показує provider, filename,
size і факт передачі аудіо перед upload; CLI вимагає
`--allow-cloud-upload`. `offline_only` блокує cloud STT і cleanup. Key material
не входить до `Settings`, worker payloads, backups або diagnostics: resolution
порядкується як `OPENAI_API_KEY`, потім OS Keychain/Credential Manager.

## 4. Потоки та стан

- Tkinter працює тільки в main thread.
- UI controller працює у background thread, а engine runtime — в окремому persistent
  `spawn` process, сумісному з macOS і Windows.
- `EngineManager` у worker process кешує runtime між задачами.
- Cancel/timeout завершує process; наступна задача створює чистий worker.
- Import/finalize/storage лишаються в parent process, тому user original не видаляється.
- Події worker → UI передаються через `queue.Queue`.
- Одночасний запуск двох транскрибувань UI блокує.

## 5. Storage

SQLite зберігає індексовані поля та повний JSON payload. Аудіо дедуплікується за SHA‑256. Видалення керованої копії враховує інші transcript records із тим самим hash.

SQLite використовує `PRAGMA user_version`. `.voice-backup` містить versioned manifest,
JSONL records, SHA‑256 inventory та опційні managed copies. Restore зберігає попереднє
сховище в recovery directory.

Підміна сховища під час restore — дві операції rename. Перед першою на диск
атомарно пишеться restore journal (`.<data_root>.restore-journal.json`) зі
шляхами, лічильником записів і міткою етапу; секретів і тексту транскриптів він
не містить. `recover_interrupted_restore()` викликається до першого відкриття
`LocalStore` у GUI та CLI і детерміновано або доводить підміну до кінця, або
відкочує її. Recovery directory не видаляється автоматично ніколи.

## 6. Межі поточної версії

- hard cancellation виконується на process boundary, не всередині model kernels;
- transcription progress фазовий, без точного відсотка inference;
- немає diarization;
- ручна правка суцільного тексту не перерозподіляється автоматично по subtitle segments;
- немає підписаних native installers;
- точність розпізнавання не заявляється без закритого benchmark.
