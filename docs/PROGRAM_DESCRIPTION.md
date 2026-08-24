# VOICE Studio — опис програми

## Статус документа

Цей документ описує фактичний W1 стан після інтеграції
`672577ef63d6107b7f7a78910574924dc9f2775f`. Customer-facing brand — **VOICE
Studio**; package/storage/CLI compatibility names (`hermes_voice_studio`,
`hermes-voice`) збережено. Поточний продукт — локальний unsigned **Test RC
0.3.0**, а не production release. Заплановані можливості та native acceptance
позначено окремо.

## Призначення і користувачі

VOICE Studio — customer-facing назва privacy-first desktop-застосунку. Він
перетворює мовлення з аудіо, відео або мікрофона на редагований текст. Основні
користувачі: автори контенту, інженери, юристи, дослідники та команди, яким
потрібна локальна транскрипція з контрольованим експортом і збереженням
оригіналу. Внутрішні compatibility names не є окремими продуктами.

## Що програма робить сьогодні

- запускається як Tkinter GUI або CLI;
- приймає WAV/MP3/M4A/MP4 та запис із мікрофона;
- перевіряє медіа, рахує SHA-256 і створює керовану локальну копію;
- транскрибує через `faster-whisper` за замовчуванням;
- може запускати експериментальний Hermes Whisper з локального `.hws` bundle;
- може надсилати файл в OpenAI STT лише після явної згоди для конкретної дії;
- зберігає незмінний `raw_text` і окремий `corrected_text`;
- застосовує словникові заміни, ручне редагування та опціональне AI cleanup;
- має `auto_copy=false` за замовчуванням; автоматичне копіювання відбувається
  лише коли користувач явно вмикає `auto_copy`, інакше Copy є явною дією;
  disclosure про history/sync зберігається;
- зберігає історію в локальній SQLite БД і керовані аудіокопії;
- експортує TXT, Markdown, JSON, SRT і VTT;
- створює та відновлює локальні backup-архіви;
- керує локальним каталогом моделей і перевіряє їхню цілісність;
- надає діагностичний звіт і CLI-команди перевірки стану.

## Основні сценарії

### Транскрипція файлу

`файл -> перевірка формату/декодування -> SHA-256 і керована копія -> вибір engine -> окремий model worker -> сегменти/текст -> словникова корекція -> SQLite -> експорт`

Оригінал користувача не видаляється. Політика retention стосується лише керованої копії.

### Запис із мікрофона

`мікрофон -> private recorder-owned WAV -> bounded writer -> стандартний pipeline транскрипції -> scoped cleanup temp-файлу`

Recorder пише 100 ms блоки через bounded queue на 64 блоки, має ліміт 7 200
секунд (дві години) і повертає status/drop/limit metadata. Degraded capture
показується користувачу й за замовчуванням відхиляється без запуску
транскрипції. App очищає лише tracked direct-child записи у private app-cache
directory; identity ambiguity зберігає residue та показує його шлях для
перевірки. Headless tests проходять, але реальний microphone, overflow/device
disconnect, limit і close на Windows/macOS ще **NOT RUN**.

### W1 data-safety controls

| Контроль | Поточна поведінка | Evidence state |
|---|---|---|
| Settings | Typed JSON fields; пошкоджений/неправильний тип дає recoverable error і безпечні defaults | `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` |
| Editor | Save/Discard/Cancel перед history navigation, AI cleanup, delete або close; Save змінює лише `corrected_text` | `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` |
| Clipboard | `auto_copy` off by default; automatic copy only after explicit `auto_copy` opt-in, otherwise Copy is explicit; OS history/manager/sync disclosure | `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` |
| Recorder | Private recorder-owned WAV; 100 ms / 64 blocks / two-hour cap; visible degraded warning; default rejection | `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` |
| Microphone temp | Tracked scoped cleanup on success/error/cancel/close/preflight; ambiguous identity residue retained and reported | `IMPLEMENTED_PENDING_NATIVE_ACCEPTANCE` |

Ці стани не є production/release acceptance. Вони очікують фізичну native
перевірку за матрицею в `PROJECT_AUDIT_STATUS.md`.

### Cloud STT та AI cleanup

Cloud не є автоматичним fallback. Перед STT GUI/CLI вимагає явну згоду, а `offline_only` блокує передачу. AI cleanup надсилає лише редагований текст і структуру сегментів, формує proposal, зберігає `raw_text` та має undo. Live cloud-поведінка в цьому аудиті не перевірялася.

### Backup і відновлення

Backup містить transcript history, settings, dictionary і керовані аудіофайли. Відновлення перевіряє manifest/hashes, працює через staging і зберігає recovery-копію попередніх даних. Архів не зашифрований і не має повних resource ceilings.

### Hermes model track

Repo містить окремий research-трек: manifest/data pipeline, tokenizer, Conformer encoder, autoregressive decoder, CTC/language losses, training, evaluation та `.hws` bundle. Production weights, закритий benchmark і доказана WER/CER відсутні. Hermes не є умовою desktop release на `faster-whisper`.

## Входи, виходи й дані

| Об’єкт | Поточне використання | Чутливість/довіра |
|---|---|---|
| Audio/video/microphone | джерело транскрипції | потенційно чутливий і недовірений input |
| `.hws`, model packs, registries | локальні моделі | недовірений до перевірки; SHA не доводить видавця |
| `.hvs-backup` | перенесення/відновлення даних | містить чутливі дані; може бути обміняний між користувачами |
| Settings/keychain/env | конфігурація і OpenAI credential | секрет не входить у settings/backup |
| SQLite + managed files | локальна історія й копії | plaintext у межах OS account |
| TXT/MD/JSON/SRT/VTT | результат користувача | контрольований локальний експорт |
| OS clipboard | лише явне перенесення тексту за default-off політикою | зовнішня OS/app/history/sync boundary |

## Інтеграції і системні межі

- локальні Python/Tkinter, SQLite, filesystem і multiprocessing;
- FFmpeg/PyAV для медіа;
- `faster-whisper`/CTranslate2 для основного STT;
- PyTorch для експериментального Hermes;
- sounddevice/pynput/keyring як опціональні desktop-залежності;
- OpenAI Audio/Cleanup API як явна opt-in інтеграція;
- model registries/Hugging Face як джерела моделей;
- GitHub Actions для CI, CodeQL і manual Test-RC workflow.

Програма не містить серверного API, мережевого listener, account/RBAC, tenant isolation, billing чи entitlement service.

## Розрахунки

Продукт не є професійним фізичним розрахунковим ПЗ, але має кількісні ML/DSP операції: resampling, log-mel features, timestamp quantization, parameter estimate, composite losses, learning-rate schedule, WER/CER, confidence heuristics і RTF. Їхній повний статус наведено в [ENGINEERING_CALCULATION_REGISTER.md](ENGINEERING_CALCULATION_REGISTER.md).

## Відомі обмеження

- unsigned Test RC; немає production-authoritative release;
- фізичні Windows/macOS, microphone/hotkey, frozen artifact і реальні codecs/models не підтверджено поточним аудитом;
- live OpenAI behavior не перевірено; поточний default `gpt-transcribe` підтверджено в офіційному API reference, але automated drift contract відсутній;
- Hermes desktop integration може повертати дублікати overlap для довгих записів;
- немає production Hermes weights, calibrated confidence або closed-test quality evidence;
- локальні дані та backup не мають application-level encryption;
- модельні/backup архіви мають integrity controls, але не publisher signature і не повні resource budgets;
- немає reproducible dependency lock, per-artifact SBOM/attestation та повного license payload;
- observability, update/rollback, entitlement і support operations не готові до платного продукту.
- native clipboard history/sync, фізичний microphone/device disconnect і OS
  permission behavior не перевірені; W1 headless evidence не замінює ці gates;
- identity-ambiguous recorder residue навмисно зберігається; secure deletion і
  delete-by-handle guarantee не заявляються;
- broader diagnostics disclosure, restore/shutdown lifecycle, backup
  encryption, signing, packaging і W2+ findings залишаються відкритими.

## Deployment і зрілість

Фактичний режим — локальний запуск із source або ручна Test-RC упаковка для desktop. SaaS/on-prem control plane не існує. Детальні ratings і blockers: [PROJECT_AUDIT_STATUS.md](PROJECT_AUDIT_STATUS.md).

## Комерційне бачення

Найменший ризиковий крок — підписаний local-first desktop на `faster-whisper` із paid binaries, updates і support, плюс offline/enterprise entitlement. Hermes варто залишати окремим research/model-pack треком до появи прав на corpus, immutable closed benchmark, production weights, signed packs і виміряних WER/CER/RTF/RAM. Hybrid control plane можливий пізніше; multi-tenant SaaS означає окрему серверну архітектуру, а не конфігураційну зміну цього desktop-коду.

Деталі та альтернативи: [COMMERCIALIZATION_READINESS.md](COMMERCIALIZATION_READINESS.md).
