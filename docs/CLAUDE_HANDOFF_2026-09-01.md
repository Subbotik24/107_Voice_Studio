# VOICE Studio — handoff для Claude

Дата: 2026-09-01. Гілка: тільки `main`. Актуальний verified commit:
`1782fa7` (`feat: add local Whisper recognition hints`).

## Що вже завершено

- Центральна навігація Dashboard / Studio / Dictionary / History; Dashboard є
  стартовою сторінкою, History відкриває запис у Studio, dirty-editor guard
  працює. Коміт: `f710aef`; verification update: `e3f1704`.
- Керований словник: bounded/atomic JSON і CSV, merge/replace preview,
  конфлікти, canonical order, `use_as_hint`, повна Dictionary page,
  external-read-only/import-copy, Add/Edit/Delete/reorder/search/test sentence,
  dirty guard, uk/cs/en. Коміт: `00fb4b4`.
- `TranscriptionHints`: bounded 256 terms / 8192 UTF-8 bytes, parent → worker
  serialization без `dictionary_path`, Local Whisper `hotwords`, Ollama/OpenAI
  ignore, no-hint leakage у IPC errors/provider calls, model reuse, незмінний
  `raw_text` і deterministic correction. Коміт: `1782fa7`.
- Останній controller gate на `1782fa7`: compileall PASS, Ruff PASS, Help PASS,
  pytest **954 passed / 9 skipped**; skips — тільки Windows symlink privilege
  `WinError 1314`. `pip check`, diff check і secret/private-path scan PASS.

Повний журнал: `docs/verification/2026-09-01-usability-pack.md`.
Затверджений master plan:
`docs/superpowers/plans/2026-09-01-usability-pack.md`.

## Правила продовження

1. Прочитати `AGENTS.md` і шість обов'язкових документів. Не створювати гілки
   або worktrees: `main` — єдина гілка.
2. Перед початком перевірити `git status --short` і `git log -3 --oneline`.
   На чистому `1782fa7` не повторювати full gate: його доказ уже в журналі.
3. Працювати increments. RED → focused GREEN → high-level review → один
   controller full gate → commit → push у `main` → запис у verification log.
4. Виконавці не роблять Git-мутацій. Якщо Claude є primary controller, commit
   і push після власної перевірки робить саме Claude.
5. Економний режим: проста механічна робота — швидка модель; multi-file
   integration — середня; найсильніша модель лише для керування й фінального
   review. Не запускати однаковий full gate на незміненому production HEAD.
6. Не змінювати transcript/backup schema, `raw_text`, originals, privacy-default
   або dependencies без нового явного рішення. Native/packaged gates, яких не
   запускали, чесно лишати `NOT_RUN`.

## Наступний логічний increment — Task 4

Реалізувати Dashboard statistics і комбіновані History filters. Рекомендовано
розбити на 4A pure/storage і 4B UI, але зробити один controller full gate та
один commit після обох частин.

### 4A — storage contracts

- Додати immutable `DashboardStatistics` і `HistoryFilter`.
- `LocalStore.statistics()` потоково читає всю історію, не використовує UI
  limit 250 і не потребує migration.
- Метрики: total/success/failed/invalid, audio seconds, Unicode word count,
  retained audio, activity 7/30 days, language/engine/model distributions,
  weighted RTF і speed multiplier.
- Word token: Unicode alphanumeric; внутрішній apostrophe/hyphen не розбиває
  слово. Invalid payload входить тільки у total+invalid і не валить Dashboard.
- Weighted RTF = `sum(audio_seconds * rtf) / sum(audio_seconds)` лише для
  валідних positive values; speed = `1 / weighted_rtf`.
- Розширити `LocalStore.list(..., filters=HistoryFilter)` або еквівалентний
  typed API. Text/date/language/engine/model/status/retained-audio filters
  комбінуються **до** `limit=250`.
- Тести: empty, 250+ rows, legacy/invalid payload, exact UTC 7/30 boundaries,
  Unicode words, failed records, RTF, filter combinations і limit ordering.

### 4B — Dashboard/History UI

- Замінити Dashboard placeholder: KPI, 7/30 activity, retained audio, top
  languages/engines/models, five recent records. Лише Tk/ttk, без chart library.
- Invalid count показувати окремо з дією/підказкою Storage Audit.
- History page: controls для всіх `HistoryFilter`; відкриття запису → Studio.
- Refresh Dashboard після save/delete/restore/import і при відкритті сторінки;
  background polling не додавати.
- Зберегти responsive sidebar і uk/cs/en. Додати headless lifecycle tests.

Після review: один full gate, commit/push, append Increment 4 у verification
log. Physical Tk smoke можна відкласти до Task 8 і позначити `NOT_RUN`.

## Що лишиться після Task 4

### Task 5 — editor tools

- Pure module: Unicode Find/Replace, case/whole-word, preview count, Replace one
  / all, segment-offset mapping, один bounded manual Undo.
- «Додати до словника» з selection: зберегти правило й застосувати лише до
  поточного transcript; іншу history не змінювати.
- Filler cleanup тільки після preview з per-match checkbox. Safe defaults:
  uk `ем`, `е-е`, `мм`; cs `ehm`, `hm`; en `um`, `uh`, `erm`, `hmm`.
  Не видаляти за default `ну`, `like`, `you know`.

### Task 6 — confidence review

- Панель сегментів нижче 0.60; threshold 0.00–1.00 тільки page state, не
  Settings. Lowest-first; `None` = «оцінки немає».
- Focus corrected segment і Play segment hook. Не називати confidence
  ймовірністю помилки та не заявляти accuracy.

### Task 7 — local playback

- Backend на наявних PyAV + sounddevice, bounded PCM chunks ~100 ms.
- play, pause/resume, stop, seek ±5 s, 0.75/1/1.25/1.5/2x, segment start.
- Speed через resampling, без pitch-preserving DSP.
- Тільки safe retained audio; external original не шукати/не мутувати.
- Page/transcript switch, restore і shutdown stop/close; abort розблоковує
  device write, thread завершується до 2 s. Fake-backend lifecycle tests.

### Task 8 — documentation і acceptance

- Узгодити README, Architecture, Security, Implementation Status, Roadmap,
  Help uk/cs/en і цей verification log.
- Один Windows packaged build: `scripts/build_windows.ps1`, frozen probe,
  clean-profile GUI та physical smoke Dashboard/Dictionary/hotwords/playback.
- На macOS: source/package GUI і physical playback smoke до cross-platform RC.
  Якщо macOS недоступний — лишити `NOT_RUN`, не заявляти cross-platform RC.
- Фінальний security/privacy review, SBOM determinism, packaging regression,
  full gate, commit/push, чисте дерево.

## Готовий стартовий prompt для Claude

> Продовж VOICE Studio з verified `main` commit `1782fa7`. Прочитай AGENTS.md,
> `docs/CLAUDE_HANDOFF_2026-09-01.md`, master plan і verification log. Не
> створюй гілок/worktrees і не повторюй full gate на незміненому `1782fa7`.
> Реалізуй лише Task 4 як два slices: 4A `DashboardStatistics` +
> `HistoryFilter` у storage з pure focused tests; після review — 4B Dashboard
> і History filter UI з uk/cs/en та headless lifecycle tests. Збережи transcript
> schema, raw_text, backups, originals і privacy defaults. Виконавці не роблять
> Git-мутацій; primary controller після одного фінального full gate робить
> commit/push у main і дописує verification log. Після Task 4 зупинися та
> відзвітуй, не починай editor/playback автоматично.

