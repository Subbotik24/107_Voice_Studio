# План доведення VOICE Studio до кінцевого стану

**Дата:** 2026-09-02. **Ревізія аудиту:** `7a18328` (`origin/main`, build `2026-09-02.3`,
версія `0.3.0rc1`). **Метод:** скіл `deep-program-audit` (DISCOVER → MODEL → AUDIT →
VERIFY → ROOT CAUSE → REPORT), Linux-контейнер, без змін production-коду.

> **Гілка:** `main` — єдина (`CLAUDE.md`, `AGENTS.md`, хуки в `.claude/hooks/`).
> Мова документа — українська за рішенням власника проєкту.

---

## 0. Головний висновок

Проєкт **не є «повністю доробленим»**. Він є **code-complete unsigned Test RC** із
зеленим source/headless гейтом на Linux і одним застарілим Windows-артефактом
(2026-08-28, до 16 наступних інкрементів). Між поточним станом і кінцевим стоять
три різні типи роботи, і їх не можна змішувати:

| Тип роботи | Де виконується | Хто | Чи досяжна з цього середовища |
|---|---|---|---|
| **A. In-repo доробка** — 2 підтверджені дефекти, документація, пайплайн, гігієна репозиторію | репозиторій | Claude | так |
| **B. Native acceptance** — Windows 10/11 x64 і macOS Apple Silicon, реальний мікрофон, Ollama, аудіопристрій, 50 задач/ОС | фізичні машини | власник | ні |
| **C. Реліз** — `models-v1`, Test RC артефакти, підпис/нотаризація, WER/CER, go/no-go, публікація | зовнішні сервіси, сертифікати, ліцензований корпус | власник + другий approver | ні |

Кінцевий стан визначено трьома документами репозиторію: `IMPLEMENTATION_STATUS.md`
§ «Release rule», `RELEASE_ACCEPTANCE.md` (7 воріт) і
`docs/release/SIGNING_KEY_CUSTODY.md` (8 зовнішніх воріт `NOT_RUN`). Жоден обсяг
роботи в репозиторії не закриває B і C. План нижче доводить A до вичерпання, а для
B і C дає покроковий протокол із критеріями приймання, щоб власник міг пройти їх
без повторного аналізу.

---

## 1. Вхідні дані: що перевірено зараз

### 1.1. Verification ledger цього аудиту

| CHECK | STATUS | COMMAND / ENVIRONMENT | RESULT | SCOPE LIMIT |
|---|---|---|---|---|
| compileall | PASS | `python -m compileall -q src tests scripts packaging` (CPython 3.11, одноразовий venv) | exit 0 | source only |
| Ruff | PASS | `python -m ruff check src tests scripts packaging` | «All checks passed» | source only |
| Help validation | PASS | `python scripts/check_help.py` | 13 Markdown files | structure, не зміст |
| Workflow pins | PASS | `python scripts/check_workflow_pins.py .github/workflows` | policy passed | — |
| wheel | PASS | `python -m build --wheel --no-isolation` | `voice_studio-0.3.0rc1-py3-none-any.whl` | — |
| pip check | PASS | `python -m pip check` | no broken requirements | venv аудиту |
| pip-audit | PASS | `python -m pip_audit` | no known vulnerabilities | продуктові залежності |
| git diff --check | PASS | — | чисто | — |
| pytest, CPython 3.11 без Tk | FAIL | `PYTHONPATH=src pytest -q` | 17 collection errors (`ModuleNotFoundError: tkinter`) | набір тестів жорстко вимагає Tk на етапі імпорту |
| pytest, CPython 3.12 + python3-tk, Xvfb | PASS | `PYTHONPATH=src xvfb-run -a pytest -q -rs` | 1379 passed, 14 skipped (Windows junctions unavailable: backup 2, model_catalog 7, storage 5), 87.7 s | headless Linux, не native |
| Windows/macOS packaged, мікрофон, Ollama, аудіопристрій | NOT RUN | — | — | потребують машин власника |

Production-дерево після аудиту: не змінене (`git status --short` порожній; `__pycache__`
ігнорується). Одноразові артефакти: `scratchpad/audit-venv`, `scratchpad/tkvenv`,
`scratchpad/repro/repro_sync.py`, логи гейтів — поза репозиторієм.

### 1.2. Стан репозиторію

- `origin/main` = `7a18328`. Локальна гілка сесії ідентична `origin/main`; локальний
  ref `main` у цьому checkout застарілий (277 файлів позаду) — артефакт середовища,
  не репозиторію.
- На remote є **5 сторонніх гілок**, що суперечать правилу «`main` — єдина»:
  `claude/project-completion-audit-amuwv5`, `claude/voice-studio-handoff-modkvn`,
  `dependabot/github_actions/github/codeql-action/analyze-4.37.9`,
  `dependabot/github_actions/github/codeql-action/init-4.37.9`,
  `dependabot/pip/openai-gte-1.0-and-lt-4`.
- **3 відкриті PR від Dependabot** (#5 `openai <4`, #6 і #7 `codeql-action 4.37.9`) без
  тріажу з 2026-08-29.
- Останній Windows EXE зібрано на `2026-08-28` (362 тести). Після нього — усі 16
  інкрементів usability-пакета, включно з batch/smart text/sync folder, in-window
  Settings/Help, self-update launchers. **Жоден packaged артефакт не містить поточного
  коду.**

### 1.3. Джерела правди й де вони розходяться

| Твердження | Джерело | Реальність | Клас |
|---|---|---|---|
| «batch transcription — P1/R1, поза R0» | `ROADMAP.md:105`, `FUTURE_GROWTH.md:37`, `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md` § «Excluded from R0» | реалізовано в `2718096`/`767bd7f` (`batch.py`, панель «Черга») | REQUIREMENT ↔ IMPLEMENTATION конфлікт |
| README EN/UK узгоджені | `CLAUDE.md` § «Change discipline» | `README.uk.md` не має розділів «Development and checks», «Data and privacy», «Backups and restore», «Project layout»; обидва README не згадують чергу, Розумний текст, папку синхронізації | стале розходження (зафіксоване ще 2026-08-28) |
| `IMPLEMENTATION_STATUS.md` — повний перелік верифікованих інкрементів | сам документ | таблиця закінчується на «Usability 7»; інкременти 10–16 (Ollama discovery fallback, in-window Settings/Help, launchers, deep audit, batch/smart/sync, dynamics/slider/progress, real-runtime smoke) відсутні | docs drift |
| `ARCHITECTURE.md` § 2 — перелік пакетів | сам документ | немає `batch`, `smart_text`, `sync_folder`, `audio_errors` | docs drift |
| `SECURITY.md` § «Data boundaries» — усі межі даних | сам документ | немає межі «папка синхронізації» (транскрипти з `raw_text` і аудіо пишуться в папку стороннього cloud-клієнта) і межі «launcher робить `git fetch` при кожному запуску» | privacy contract gap |
| Launcher «local/private by default» | `README.md`, `SECURITY.md` | `run_windows.bat:20` і `run_mac.command:17` при кожному запуску виконують мережевий `git fetch origin` і `git checkout -f -B main origin/main`; не задокументовано ні в README, ні в Help (`grep` порожній) | undocumented behaviour |
| `RELEASE_ACCEPTANCE.md` покриває весь продукт | сам документ | немає smoke для черги, Розумного тексту, папки синхронізації, графіків Огляду, слайдера позиції, прогрес-бара, in-window Settings/Help, self-update | acceptance gap |
| «both workflow jobs bounded at 20 minutes» | `IMPLEMENTATION_STATUS.md` | `codeql.yml` без `timeout-minutes` | stale (з 2026-08-28) |
| Windows-збірка дає release manifest | неявно у `README.md` § «Test RC artifacts» | `scripts/build_windows.ps1` пише лише `SHA256SUMS.txt`; `release-manifest.json` і acceptance-result вимагає тільки `build_test_rc.sh` (macOS); `create_release_manifest.py:177` хардкодить `unsigned-macos-test-rc` | pipeline asymmetry (з 2026-08-28) |
| Baseline `PYTHONPATH=src pytest -q` | `CLAUDE.md` § «Start here» | набір не збирається без `tkinter` (17 модулів); CI-матриця лише macOS/Windows, а всі докази з 2026-09-01 — Linux | environment contract gap |

---

## 2. Підтверджені знахідки аудиту (сортовано за пріоритетом)

### F-1 · CONFIRMED BUG · P2 · confidence high — корінь папки синхронізації не перевіряється під час запису

- **LOCATION:** `src/voice_studio/app.py:4820-4840` (`_mirror_transcript_quietly`),
  `src/voice_studio/sync_folder.py:229-262` (`mirror_transcript`), `models.py:228-229`
  (`Settings.validate`).
- **PROBLEM:** `validate_sync_root` викликається лише в GUI Save (`app.py:2067`) і в
  «Синхронізувати все зараз» (`app.py:4852`). Автоматичне дзеркалення після кожного
  запису бере `Settings.sync_folder` як є. `Settings.validate()` перевіряє лише
  непорожність.
- **EVIDENCE (repro, scratch-скрипт):** `mirror_transcript(t, data/sources, …)` записав
  `data/sources/2026-09/…md/.json` **усередині приватного data root**, який
  `validate_sync_root` для того самого шляху відхиляє. Видалений після Save каталог
  (`usb_mount`) мовчки **створюється заново** через `mkdir(parents=True)`.
- **IMPACT:** `settings.json`, відредагований вручну або **відновлений з backup іншої
  машини**, обходить containment; відмонтований диск або видалена папка → дзеркало
  пишеться на локальний диск у точку монтування; підміна папки symlink-ом після Save
  не помічається. Дані не втрачаються, але межа «нічого не пишемо всередину/навколо
  data root» порушується.
- **ROOT CAUSE:** валідація належить UI-шару, а не власнику операції запису.
- **FIX DIRECTION:** `mirror_transcript`/`mirror_all` приймають лише результат
  `validate_sync_root` (тип-обгортка або повторна валідація всередині); у
  `_mirror_transcript_quietly` викликати валідацію й показувати `sync_failed` без
  створення каталогів; не робити `mkdir` для самого кореня — лише для підкаталогу
  `YYYY-MM`.
- **REGRESSION TEST:** (1) `settings.sync_folder` всередині data root → авто-дзеркало
  відмовляє, файлів немає; (2) корінь видалено після Save → відмова, корінь не
  створено; (3) корінь замінено symlink-ом → відмова.

### F-2 · CONFIRMED BUG · P3 · confidence high — шлях `~/…` або відносний шлях зберігається нерозв'язаним

- **LOCATION:** `app.py:2056-2068` (`_validate_settings_for_save`), `app.py:5543`
  (редагований `ttk.Entry` для `sync_folder`), `sync_folder.py:52-56`
  (`expanduser()` лише у валідаторі).
- **EVIDENCE (repro):** `validate_sync_root(Path("~/Drive"))` → PASS з розв'язаним
  шляхом; збережений рядок лишається `~/Drive`; `mirror_transcript(t, Path("~/Drive"))`
  створив **літеральний каталог `~` у CWD** і записав туди `.md/.json`; у справжній
  `Drive` нічого не потрапило. Те саме для відносного шляху: результат залежить від
  CWD процесу.
- **FIX DIRECTION:** Save зберігає `str(validate_sync_root(...))` (розв'язаний абсолютний
  шлях), а не введений текст.
- **REGRESSION TEST:** ввести `~/x` у полі → у `settings.json` абсолютний шлях;
  дзеркало пише у справжній каталог.

### F-3 · CONFIRMED (contract) · P3 · confidence high — launchers роблять мережевий self-update без згоди й документації

- **LOCATION:** `run_windows.bat:8-30`, `run_mac.command:8-27`.
- **PROBLEM:** при кожному запуску `git fetch origin` (мережа, GitHub) і
  `git checkout -f -B main origin/main` (примусово, після попередження без
  підтвердження; локальні правки відкидаються; будь-яка інша гілка перемикається на
  `main`). Виконується довільний код із `origin/main` без перевірки підпису. Не
  згадується в `README*.md`, `SECURITY.md`, `docs/help/*` (перевірено `grep`).
- **IMPACT:** для продукту «local/private by default» це неоголошена мережева
  активність і канал доставки коду без trust root; `offline_only` не діє на launcher.
- **FIX DIRECTION:** продуктове рішення власника: (а) залишити, але зробити opt-in
  через змінну/прапорець (`VOICE_STUDIO_AUTO_UPDATE=1`) і задокументувати в README,
  SECURITY.md § «Data boundaries», Help → Troubleshooting; або (б) винести в окремий
  `update_*.bat/.command`. У будь-якому разі: без `-f` над локальними змінами
  (стоп із поясненням), перевіряти `origin` вказує на канонічний репозиторій.
- **REGRESSION TEST:** розширити `tests/test_launchers_app.py` контрактом на
  прапорець і на відмову при локальних змінах.

### F-4 · CONFIRMED · P3 · docs — документація відстала від коду на 7 інкрементів

Повний перелік у § 1.3. Найважливіше: `FUTURE_GROWTH.md` і `ROADMAP.md` досі
називають batch transcription майбутньою R1-функцією; `SECURITY.md` не описує межу
даних папки синхронізації (це єдиний шлях, яким `raw_text` і аудіо навмисно
потрапляють у папку, що синхронізується в хмару стороннім клієнтом, і який **не
видаляє** дзеркальні файли після видалення запису з Історії — Help це каже,
SECURITY.md ні).

### F-5 · LIKELY ISSUE · P4 · confidence medium — дзеркалення аудіо виконується синхронно в Tk-потоці

- **LOCATION:** `app.py:2984`, `:3198`, `:3731`, `:4896` → `_mirror_transcript_quietly`
  → `sync_folder._atomic_copy_file` (copy + `fsync`).
- **PROBLEM:** з увімкненим «Копіювати також аудіо» після кожного завершеного файла
  (зокрема в черзі) головний потік копіює керовану копію (стеля контейнменту 2 GiB) у
  папку cloud-клієнта, який може тримати файл або тротлити. UI замерзає на час копії.
  Не виміряно — тому LIKELY.
- **FIX DIRECTION:** переносити дзеркалення на `_start_worker("sync-auto")` з чергою
  «останній стан перемагає», статус у рядку стану.

### F-6 · LIKELY ISSUE · P4 — `update_speaker_labels` не атомарний

`storage.py:685-706`: `get()` → зміна metadata → `save()` без однієї транзакції
(на відміну від `update_editor_state`, що читає під `BEGIN IMMEDIATE`). У GUI обидва
шляхи серіалізовані головним потоком; ризик лише для одночасного CLI+GUI. Виправити
за зразком `update_editor_state`, тест на lost update.

### F-7 · CONFIRMED · P4 · pipeline/process

- `codeql.yml` без `timeout-minutes` (`IMPLEMENTATION_STATUS.md` стверджує інакше).
- `build_windows.ps1` не генерує `release-manifest.json` і не вимагає acceptance-result;
  `create_release_manifest.py:177` хардкодить `release_kind = unsigned-macos-test-rc`.
- Версія `0.3.0rc1` продубльована у 6 місцях (`pyproject.toml`, `__init__.py`,
  `voice_studio.spec`, `create_release_manifest.py`, `build_windows.ps1`,
  `build_test_rc.sh`) плюс `APP_BUILD` в `app.py:103` вручну.
- 5 сторонніх remote-гілок і 3 неопрацьовані Dependabot PR.
- Baseline у `CLAUDE.md` не каже, що набір тестів вимагає Tk; CI не має Linux-джоби,
  хоча всі докази останнього місяця — Linux.

### Перевірено чистим (evidence)

- `diagnostics()` (`diagnostics.py:102-181`) не серіалізує `Settings`, тому абсолютний
  шлях папки синхронізації і моделі **не** потрапляють у діагностичний експорт.
- Дзеркальний JSON виключає `source_path` (`sync_folder.py:246`); ключів доступу в
  `Transcript.to_dict()` немає (`models.py:73-95`).
- `_close()` (`app.py:6165-6273`) ставить чергу на паузу до скасування задачі, закриває
  playback/hotkey/recorder/job controller, приєднує воркери з бюджетом.
- `BatchQueue` — чиста модель з явними переходами; помилка одного файла не зупиняє
  чергу; Cancel → `failed` + pause (`app.py:3590-3603`).
- Батч використовує той самий `_process(..., batch=True)` шлях, тобто ту саму
  per-file cloud-consent і ту саму обробку помилок (`app.py:3540`).
- Продуктове рішення про виняток `offline_only` для CLI `--engine openai-cloud
  --allow-cloud-upload` тепер узгоджене в `CLAUDE.md`, `SECURITY.md`, `cli.py:595-605`.

---

## 3. Обмеження, які план не порушує

- `raw_text` незмінний; оригінал користувача ніколи не видаляється; local/private by
  default; жодних секретів у Git/налаштуваннях/backup/diagnostics.
- Жодних нових залежностей без окремого рішення; жодних змін схеми transcript/backup
  без migration note і regression tests.
- `main` — єдина гілка; push тільки `git push origin main`.
- Не заявляти точність без ліцензованого закритого корпусу; не називати unsigned Test
  RC production-релізом; `NOT RUN` лишається `NOT RUN`, доки немає доказу з реальної
  машини/сервісу.

---

## 4. Рішення: етапи до кінцевого стану

> **Стан на 2026-09-02 (вечір):** R0.11, R0.12, R0.14 і in-repo частина R0.13
> виконані одним раундом (гейт `1389 passed`, 14 junction skips; запис у
> `VERIFICATION.md` § «In-repo completion gate»). Прийняті припущення замість
> рішень § 6: F-3 → opt-in `VOICE_STUDIO_AUTO_UPDATE=1`; batch/smart text/sync
> у R0 (amendment у spec); Dependabot #6/#7 перенесено, #5 не перенесено.
> Не виконано з цього середовища: видалення 5 сторонніх remote-гілок (git-проксі
> сесії пропускає лише `main`), branch protection, F-5 (дзеркалення аудіо на
> worker — лишається P4). Далі — лише етапи власника: W5-A/B, W4-M, W4-R, P0.6,
> W4-S, W6.

Нумерація продовжує R0 (R0.1–R0.10 закриті source/headless). Кожен in-repo етап =
RED → GREEN → review → один controller full gate → commit → `git push origin main` →
запис у `docs/verification/`. Кожен зовнішній етап має протокол і формат доказу.

### Етап R0.11 — виправлення підтверджених дефектів (in-repo, Claude)

**Мета:** закрити F-1, F-2, F-6 із failing-first тестами; ухвалити й реалізувати
рішення щодо F-3.

1. `sync_folder.py`: `mirror_transcript`/`mirror_all` перевіряють корінь власною
   валідацією (той самий `validate_sync_root` з `data_root`), не створюють корінь,
   створюють лише `YYYY-MM`. Тести з § F-1.
2. `app.py` `_validate_settings_for_save`: зберігати розв'язаний абсолютний шлях;
   тест з § F-2. Help uk/cs/en → reference «Синхронізація»: одне речення про те, що
   шлях зберігається абсолютним.
3. `storage.py` `update_speaker_labels` під однією транзакцією `BEGIN IMMEDIATE`;
   тест на lost update.
4. F-3 (потребує рішення власника, див. § 6): реалізувати вибраний варіант у обох
   launchers + `tests/test_launchers_app.py` + README/SECURITY/Help.
5. За бажанням у той самий етап: F-5 — авто-дзеркалення на worker (окремий commit,
   окремий тест на те, що головний потік не блокується на fake-повільному копіюванні).

**Приймання:** full gate PASS (compile, Ruff, `check_help.py`, pytest без нових
skip-ів, wheel, `pip check`, `git diff --check`); нові тести падають до фіксу;
`VERIFICATION.md` і `docs/verification/2026-09-01-usability-pack.md` доповнені
інкрементом 17.

### Етап R0.12 — узгодження документації й scope (in-repo, Claude)

**Мета:** усі публічні документи описують поточний продукт; конфлікт «batch — це R1»
розв'язано явно.

1. `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md`: додати
   розділ «Amendment 2026-09-02»: batch queue, smart text (speaker labels у metadata),
   sync folder, dashboard dynamics, in-window Settings/Help включені до R0 рішенням
   власника (посилання на комміти). `FUTURE_GROWTH.md` і `ROADMAP.md` P1: прибрати
   batch, лишити «відновлення черги після перезапуску» і «scheduler/budgets» як R1.
2. `README.md` + `README.uk.md`: розділи «Черга», «Розумний текст», «Папка
   синхронізації», «Оновлення при запуску» (за F-3); `README.uk.md` доповнити чотирма
   відсутніми розділами; перевірити паритет заголовків (`diff` списків `## `).
3. `ARCHITECTURE.md` § 2: рядки `batch`, `smart_text`, `sync_folder`, `audio_errors`;
   § 4: авто-дзеркалення (після F-5 — на worker).
4. `SECURITY.md` § «Data boundaries»: папка синхронізації (що пишеться: `raw_text`,
   `corrected_text`, metadata зі speaker labels, опційно аудіо; чого не пишеться:
   `source_path`, ключі; дзеркало не видаляється при видаленні запису; папка
   валідована на існування/symlink/containment); launcher self-update (за F-3).
5. `IMPLEMENTATION_STATUS.md`: рядки для інкрементів 10–17 із точними числами гейтів;
   виправити твердження про timeouts CI; оновити «Highest-priority next work» у
   `CLAUDE.md`.
6. `RELEASE_ACCEPTANCE.md`: додати smoke-пункти для черги (2+ файли, Cancel, Skip,
   помилка одного файла), Розумного тексту (спікер, експорт MD), папки синхронізації
   (реальний Google Drive/OneDrive клієнт, `~`-шлях, вимкнення папки), графіків,
   слайдера, прогрес-бара, in-window Settings/Help, self-update (онлайн/офлайн).
7. `docs/help/{uk,cs,en}`: `/sync-help` після пунктів 1–6; `check_help.py` PASS;
   ручна перевірка паритету змісту трьома мовами по розділах (не лише ключів).

**Приймання:** `grep -n -i 'batch\|черг\|sync\|синхрон'` дає збіги в усіх названих
документах; гейт PASS; commit + push.

### Етап R0.13 — пайплайн, CI і гігієна репозиторію (in-repo, Claude; частина — власник)

1. `codeql.yml`: `timeout-minutes: 20`.
2. `ci.yml`: додати джобу `ubuntu-24.04` × CPython 3.12 із `apt-get install
   python3-tk xvfb` і `xvfb-run pytest` — щоб CI відтворював середовище, у якому
   зібрано всі докази вересня. Залишити macOS/Windows.
3. `CLAUDE.md`/`AGENTS.md` § baseline: явно вказати вимогу Tk (`python -c "import
   tkinter"`) і `xvfb-run` для headless Linux.
4. Windows-пайплайн: `build_windows.ps1` викликає `create_release_manifest.py` з
   параметром `--release-kind unsigned-windows-test-rc` (додати аргумент замість
   хардкоду) і вимагає `VOICE_STUDIO_ACCEPTANCE_RESULT` симетрично з macOS.
5. Одне джерело версії: `__version__` у `voice_studio/__init__.py`, читання його зі
   `spec`, скриптів і manifest; тест `test_release_manifest_app.py` на узгодженість.
   `APP_BUILD` генерувати зі `git describe`/дати збірки під час пакування, а не
   редагувати вручну.
6. **Власник:** тріаж Dependabot #5 (`openai <4` — перевірити сумісність
   `engines/openai_cloud.py`, `cloud_cleanup.py` з 2.x/3.x API), #6/#7 (CodeQL
   4.37.9 — механічне оновлення SHA-пінів). Правило репозиторію — без сторонніх гілок:
   зміни переносяться в `main` окремими коммітами, PR закриваються, гілки
   видаляються (`git push origin --delete …`), так само дві `claude/*` гілки.
   Розглянути вимкнення Dependabot-гілок на користь `security-updates`-only або
   групового щомісячного оновлення, інакше правило «`main` — єдина» ламатиметься
   автоматично щотижня.
7. Branch protection на `main` (потрібно для W4/W6): required checks = CI matrix +
   CodeQL; це узгоджується з `SIGNING_KEY_CUSTODY.md` гейтом «branch protection».

**Приймання:** CI зелений на трьох ОС; `git ls-remote --heads origin` показує лише
`main`; Dependabot PR закриті з рішенням у коммітах.

### Етап R0.14 — фінальний in-repo гейт і фіксація стану (in-repo, Claude)

1. Повторний `deep-program-audit` лише по diff R0.11–R0.13 (skeptic pass).
2. Full gate на чистому `main`; `pip-audit`; SBOM детермінізм (два прогони
   `generate_sbom.py` byte-identical).
3. `VERIFICATION.md`: новий розділ «In-repo completion — <дата>» з ledger; явно
   перелічити все `NOT RUN`.
4. Стан «**in-repo scope exhausted**» фіксується коммітом `docs: record in-repo
   completion gate`. Після цього in-repo робота вважається завершеною; далі лише
   виправлення з native acceptance.

### Етап W5-A — native acceptance на Windows 10/11 x64 (власник, ~1 робочий день)

**Передумови:** чистий `main` після R0.14; Windows-машина з Python 3.12 x64, Ollama з
audio-capable моделлю (`gemma4:12b` як у попередньому прогоні), мікрофон, динаміки,
Google Drive або OneDrive клієнт, FFmpeg за бажанням.

**Протокол** (кожен крок → рядок у `acceptance-result.json` через
`scripts/platform_acceptance.py` або ручний запис у release issue без приватних даних):

1. Source launcher: `run_windows.bat` двічі — онлайн (self-update, реінсталяція
   залежностей після зміни HEAD) і офлайн (запуск поточної версії). Перевірити, що
   вікно показує `APP_BUILD` поточного `main`.
2. Packaged: `build_windows_exe.bat` → `dist\0.3.0-test-rc1-windows-x64\`; frozen
   runtime probe і GUI smoke проходять автоматично; записати SHA-256 EXE і ZIP.
3. Clean-machine: розпакувати ZIP на другій машині/VM без Python; запуск, Settings →
   профілі, Help F1 трьома мовами.
4. Мікрофон: запис 10 с і 5 хв; hotkey старт/стоп; відключення пристрою під час
   запису; ліміт 2 год не перевіряти (лише логіка попередження).
5. Файли: WAV/MP3/M4A/MP4 по одному в Studio; ті самі 4 через чергу; Cancel
   посеред черги; Skip; помилка на пошкодженому файлі; перевірити, що оригінали
   незмінні (SHA-256 до/після).
6. Мови: uk/cs/en/auto на реальних записах; Ollama vs Local Whisper (tiny) для
   таймкодів; експорт TXT/MD/JSON/SRT/VTT; ручна правка → SRT відображає правку.
7. Usability-пакет за `RELEASE_ACCEPTANCE.md` (оновленим у R0.12): Огляд/графіки,
   Історія (фільтр за межею 250 записів), Словник (JSON/CSV, hotwords), інструменти
   редактора, панель впевненості, playback через реальний пристрій (play/pause/seek/
   speed/slider), Розумний текст (спікери, експорт), папка синхронізації через
   реальний Drive/OneDrive клієнт (перевірити, що файл піднявся в хмару лише через
   клієнт, `source_path` відсутній, видалення запису не видаляє дзеркало).
8. Backup: v1 і v2 (`--encrypt`) create/verify/restore; restore під час відкритої
   сторінки Settings; переривання restore (kill process) → recovery при наступному
   старті; `models/` і `exports/` збережені.
9. Storage: `storage audit`, `models reconcile`, `repair-missing` на штучно
   зламаному стані.
10. 50-task серія: `scripts/platform_acceptance.py` (0 crash, originals unchanged).
11. OpenAI (одноразово, публічний домен, з живим ключем власника): STT
    `--allow-cloud-upload` і cleanup proposal/apply/undo; перевірити, що ключ не
    потрапив у settings/backup/diagnostics (`grep` по каталогах даних).

**Критерій PASS:** усі пункти без crash; `acceptance-result.json` без приватних
шляхів; кожен знайдений дефект → issue → виправлення в `main` → повторний прогін
лише порушених пунктів.

### Етап W5-B — native acceptance на macOS Apple Silicon (власник, ~1 робочий день)

Той самий протокол з відмінностями: `run_mac.command`; `scripts/build_test_rc.sh`
(вимагає `VOICE_STUDIO_ACCEPTANCE_RESULT`, тому спочатку 50-task серія через
source, потім збірка `.app/.dmg`); ad-hoc `codesign` перевірка; Gatekeeper-попередження
очікуване; Accessibility permission для hotkey; мікрофонний permission-prompt
(`NSMicrophoneUsageDescription`); high-DPI/два монітори.

### Етап W4-M — release assets `models-v1` (власник, ~2 години, машина з доступом до Hugging Face)

За `docs/release/MODELS_V1_RUNBOOK.md` дослівно: pin SHA `Systran/faster-whisper-tiny`
і `-small`, `snapshot_download` по HTTPS, `scripts/build_model_release.py` →
ZIP + `SHA256SUMS.txt` + `model-registry-v1.json`, GitHub Release `models-v1`,
повторне завантаження і звірка SHA-256. Потім у застосунку:
`VOICE_STUDIO_MODEL_REGISTRY_URL` → `models install tiny` на Windows і macOS (це
частина W5). Жоден архів у Git.

### Етап W4-R — Test RC артефакти й тег (власник + Claude для чек-листа)

1. Лише після W5-A, W5-B, W4-M зелених: зібрати фінальні артефакти з того самого
   комміта на обох ОС; `SHA256SUMS.txt`, `release-manifest.json` (обидві ОС після
   R0.13), SBOM, acceptance-result.
2. `release.yml` спрацьовує на тег `v*`: перевіряє гейт і відсутність
   медіа/DB/великих файлів; публікація артефактів — ручний крок за
   `RELEASE_ACCEPTANCE.md`.
3. Тег `v0.3.0-rc1` тільки коли всі 7 воріт `RELEASE_ACCEPTANCE.md` мають докази у
   release issue. Артефакти позначені unsigned; SmartScreen/Gatekeeper попередження
   описані в release notes.

### Етап P0.6 — benchmark WER/CER (власник; паралельно з W5)

Harness `benchmark.py` і `scripts/…` готові; бракує **ліцензованого закритого корпусу**
uk/cs/en за `docs/DATA_GOVERNANCE.md` (consent, ліцензія, record_id). Мінімум: 30
записів на мову, 5–60 с, різні мікрофони; профілі `tiny/small` (Whisper) і
`gemma4:12b` (Ollama); метрики WER/CER/RTF/RAM/model load для Mac mini M4 і Windows
target. Результат — таблиця у `VERIFICATION.md` § «Benchmark» без маркетингових
заяв; без корпусу розділ лишається `NOT RUN`, і README продовжує не заявляти
точність.

### Етап W4-S — підпис і нотаризація, 0.4 production candidate (власник + другий approver)

Строго за `docs/release/SIGNING_KEY_CUSTODY.md`; 8 воріт, усі `NOT_RUN`:

1. Apple Developer Program → Developer ID Application → нотаризація `.dmg`, staple.
2. Azure Trusted Signing → підпис `.exe`/ZIP-вмісту, timestamp.
3. Offline Ed25519 ключ для update-manifest (потрібен лише якщо ухвалено канал
   оновлень R2; для 0.4 без updater цей гейт можна позначити `N/A` явним рішенням).
4. Protected GitHub release environment + branch protection (R0.13 п. 7).
5. Юридичне/ліцензійне схвалення назви, інсталятора, включених моделей (ліцензії
   Systran/Whisper у `THIRD_PARTY_NOTICES.md`).
6. Rollback drill.
7. Версія → `0.4.0`; після цього `Development Status :: 4 - Beta` у `pyproject.toml`.

### Етап W6 — незалежне go/no-go і публікація

Другий approver перевіряє: source revision, SHA-256 артефактів, докази W5-A/W5-B,
підписи, SBOM, відсутність секретів. Рішення фіксується у release issue. Лише після
цього — публічний GitHub Release із signed артефактами. Це і є **кінцевий стан**.

### Після кінцевого стану — режим супроводу

- Щомісячний груповий тріаж Dependabot у `main` без гілок; `pip-audit` у CI як hard
  fail (уже так).
- Повторний `deep-program-audit` перед кожним мінорним релізом.
- R1-кандидати (`FUTURE_GROWTH.md` після R0.12): відновлення черги після перезапуску,
  word timestamps, split/retime редактор, diarization — кожен зі своїм spec/privacy
  review, не раніше 0.4.

---

## 5. Перевірка: як зрозуміти, що етап закритий

| Етап | Об'єктивний доказ |
|---|---|
| R0.11 | нові тести RED→GREEN у логі; full gate PASS; repro-сценарії § F-1/F-2 більше не відтворюються |
| R0.12 | `diff` заголовків README EN/UK порожній по структурі; `grep` нових функцій у всіх 9 документах; `check_help.py` PASS |
| R0.13 | CI зелений на ubuntu/macos/windows; `git ls-remote --heads origin` = лише `main`; версія читається з одного місця (тест) |
| R0.14 | розділ у `VERIFICATION.md` з ledger і `NOT RUN` списком; чисте дерево |
| W5-A/B | `acceptance-result.json` на кожну ОС, 50 задач, 0 crash, SHA-256 оригіналів незмінні |
| W4-M | GitHub Release `models-v1` з `SHA256SUMS.txt`, звірка після повторного завантаження |
| W4-R | тег + артефакти + manifest + SBOM + acceptance у release issue |
| P0.6 | таблиця WER/CER із посиланням на ліцензію корпусу |
| W4-S | нотаризаційний ticket, signtool/`codesign --verify` вивід, записи custody |
| W6 | підписане рішення approver-а в release issue |

---

## 6. Рішення, які потрібні від власника (блокують відповідні етапи)

1. **F-3 launcher self-update:** opt-in прапорець, окремий скрипт оновлення, чи
   залишити як є з документуванням. Рекомендація: opt-in через змінну середовища +
   документація; без `-f` над локальними змінами.
2. **Scope-amendment R0:** підтвердити, що batch/smart text/sync folder входять у R0
   (фактично вже в `main`), щоб оновити spec/ROADMAP/FUTURE_GROWTH.
3. **Dependabot:** закрити PR і гілки вручну в `main`, чи вимкнути/обмежити
   Dependabot. Рекомендація: `security-updates` only + щомісячний ручний груповий
   апдейт.
4. **Update channel (Ed25519 гейт):** потрібен для 0.4 чи відкладається на R2. Без
   рішення гейт лишається `NOT_RUN` і блокує W4-S формально.
5. **Корпус для WER/CER:** чи є ліцензовані записи; без них P0.6 не стартує.

---

## 7. Ризики

- **Windows-артефакт застарів на 16 інкрементів** — найбільший ризик регресії саме в
  packaged-режимі (нові модулі, `sounddevice` без пристрою, sync у Drive-папку, Tk
  in-window сторінки на high-DPI). Тому W5-A слід починати одразу після R0.11, не
  чекаючи R0.12–R0.13.
- **Self-update launcher** без рішення F-3 — репутаційний ризик для «privacy-first»
  і канал доставки неперевіреного коду; це також суперечить `offline_only`.
- **Dependabot** щотижня ламає правило «`main` — єдина» автоматично.
- **Ollama і мови:** `gemma4:12b` не транскрибував кирилицю в попередньому прогоні;
  W5 має явно перевірити uk/cs через Ollama і зафіксувати обмеження в README, або
  зробити Local Whisper рекомендованим для uk/cs.
- **Аудіо в папці синхронізації:** користувач сам вибирає cloud-папку, але дзеркало
  не видаляється при видаленні запису — без явного тексту в SECURITY.md і Help це
  сприйматиметься як витік.
- **Тести без Tk не збираються** — будь-який новий агент/CI на Linux без
  `python3-tk` отримає хибний «FAIL» і може почати «лагодити» неіснуючий дефект.

---

## 8. Синтез

Код продукту завершено на рівні source/headless, і саме тому решта роботи має
іншу природу, ніж усі попередні інкременти. У репозиторії лишилися два справжні
дефекти папки синхронізації, одне неоголошене мережеве рішення в launcher-ах,
відставання документації на сім інкрементів і невелика пайплайнова асиметрія — усе
це закривається чотирма in-repo етапами R0.11–R0.14 і після них in-repo обсяг
вичерпано. Далі кінцевий стан визначають не рядки коду, а докази з фізичних машин,
release assets моделей, ліцензований корпус і підписи, і кожен із цих етапів має в
цьому плані протокол, формат доказу та критерій PASS, щоб їх можна було виконати без
повторного аналізу. Найбільша практична загроза — не в коді, а в тому, що жоден
packaged артефакт не містить поточного `main`, тому Windows acceptance варто
починати відразу після R0.11, паралельно з документацією, а не після неї.
