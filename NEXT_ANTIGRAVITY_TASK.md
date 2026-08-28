# ЗАВДАННЯ ДЛЯ ANTIGRAVITY

**TASK_ID:** W2-R1-journaled-restore-recovery
**ГІЛКА РОБОТИ:** `main`. Нових гілок **не створювати** — уся розробка й пуші
йдуть безпосередньо в `main`.
**BASELINE:** останній коміт `main`. Перед стартом виконати
`git fetch origin main && git checkout main && git pull origin main`.
**Останній перевірений зелений стан:** `d61f1d9` (`ruff` чистий, `320 passed`,
wheel і `pip check` — PASS).
**Джерело завдання:** `IMPLEMENTATION_STATUS.md` → розділ «Not implemented yet» → пункт
«journaled restore/startup recovery». Нової функціональності не вигадувати.

---

## 1. МЕТА

Зробити перервану операцію відновлення з резервної копії (`restore_backup`)
відновлюваною: записувати на диск журнал відновлення (restore journal) і при
наступному запуску застосунку автоматично **або доводити відновлення до кінця,
або відкочувати його назад**, ніколи не залишаючи користувача без даних.

Одна мета. Нічого більше в цьому етапі не реалізовувати.

---

## 2. ПОТОЧНИЙ СТАН (що вже є в коді)

Файл: `src/voice_studio/backup.py`, функція
`restore_backup(path, data_root, *, settings_target=None)`.

Поточна послідовність:

1. `verify_backup(path)` — перевірка ZIP-бюджету, метаданих і SHA-256 кожного члена.
2. `require_free_space(...)` — префлайт вільного місця.
3. Створюється **staging-каталог**: `<parent>/.<data_root.name>.restore-<uuid4hex>`.
   У ньому будується повний `LocalStore` з усіма транскриптами та `sources/`.
4. Перевірка кількості записів проти маніфесту + `store.audit()` — обидві до підміни.
5. **Підміна (swap), два окремі виклики:**
   ```python
   if data_root.exists():
       recovery = data_root.parent / f"{data_root.name}.recovery-{timestamp}-{uuid4hex8}"
       data_root.replace(recovery)          # крок A
   try:
       temporary.replace(data_root)         # крок B
   except BaseException:
       if recovery and recovery.exists() and not data_root.exists():
           recovery.replace(data_root)      # відкат у межах процесу
       raise
   ```
6. **Після** підміни записуються `settings.json` (зі збереженням копії
   `<name>.pre-restore-<timestamp>`) і, за наявності, `dictionary.restored.json`.
7. `finally:` видаляє staging-каталог, якщо він ще існує.

### Підтверджена прогалина

Обробник `except BaseException` рятує лише від помилки **всередині процесу**.
Якщо процес гине між кроком A і кроком B (втрата живлення, `kill`, збій ОС),
на диску лишається:

* **немає** `data_root`;
* осиротілий staging `.<data_root.name>.restore-<hex>` (містить нові дані);
* осиротілий `<data_root.name>.recovery-<timestamp>-<hex>` (містить старі дані);
* **жодного файлу, який пояснює, який із двох каталогів є справжніми даними.**

Наступний запуск створить порожній `LocalStore(data_dir())` — користувач побачить
порожню історію, а обидві копії лежатимуть поруч без пояснення.

### Вікна відмови, які треба закрити

| Вікно | Момент | Стан на диску | Правильна дія при старті |
|---|---|---|---|
| **W1** | після кроку A, до кроку B | немає `data_root`; є staging + recovery | **Довести до кінця**: перенести staging у `data_root` |
| **W2** | після кроку B, до запису `settings.json` | `data_root` новий, `settings.json` старий | **Дописати** збережені в журналі settings/dictionary |
| **W3** | до кроку A | `data_root` цілий; є лише staging | **Прибрати** staging, нічого не чіпати |

### Що вже працює і має лишитися без змін

* `VoiceStudioApp._queue_restore` → `job_controller.close()` → non-daemon потік →
  подія `backup_done`/`backup_error` → `_reload_after_restore()`.
* `_close()` не дає закрити застосунок, поки живий `_maintenance_thread`.
* Префлайт вільного місця, `BACKUP_ZIP_BUDGET`, звірка кількості записів, `store.audit()`.

---

## 3. SCOPE — що саме реалізувати

### 3.1. Журнал відновлення

Записувати **до кроку A**, атомарно (тимчасовий файл + `os.replace`), у
`data_root.parent / f".{data_root.name}.restore-journal.json"`.

Обов'язкові поля:

```json
{
  "journal_version": 1,
  "backup_version": 1,
  "created_at": "<ISO-8601 UTC>",
  "data_root": "<абсолютний шлях>",
  "staging_path": "<абсолютний шлях staging>",
  "recovery_path": "<абсолютний шлях recovery або null>",
  "expected_records": <int з маніфесту>,
  "settings_target": "<шлях або null>",
  "settings_payload_written": false,
  "stage": "swap_started"
}
```

Вимоги:

* Поле `stage` оновлюється на `"swap_completed"` одразу після успішного кроку B
  (перезапис журналу атомарний).
* Журнал видаляється **тільки** після повного успіху: підміна + запис
  settings/dictionary.
* Журнал не містить тексту транскриптів, ключів API, вмісту `settings.json` —
  лише шляхи, лічильники і мітки етапів. Значення `settings_payload` **не**
  зберігати в журналі; для вікна W2 зберігати settings у staging-каталозі
  до підміни (файл `.restore-settings.json` усередині staging), щоб після
  підміни він опинився всередині `data_root` і був доступний при відновленні.
* Створення журналу не повинно змінювати повернене значення при успішному
  відновленні, окрім **додаткових** ключів.

### 3.2. Функція відновлення при старті

Нова публічна функція в `src/voice_studio/backup.py`:

```python
def recover_interrupted_restore(
    data_root: Path,
    *,
    settings_target: Path | None = None,
) -> dict[str, Any]:
```

Повертає словник із щонайменше `{"status": ..., "action": ..., "records": ...,
"recovery": ...}`, де `action` ∈
`{"none", "completed", "settings_completed", "rolled_back", "staging_discarded"}`.

Алгоритм (детермінований, без інтерактиву):

1. Журналу немає → `{"status": "PASS", "action": "none"}`. Жодних побічних ефектів.
2. Журнал є, але пошкоджений / несумісний `journal_version` → **нічого не
   видаляти**, повернути `{"status": "FAIL", ...}` з поясненням.
3. `stage == "swap_started"`:
   * `data_root` **не існує**, staging існує і його `LocalStore.audit()` дає `PASS`,
     а кількість записів дорівнює `expected_records` → перенести staging у
     `data_root` (`replace`), дописати settings, видалити журнал →
     `action = "completed"`.
   * `data_root` **не існує**, staging відсутній або не проходить перевірку,
     recovery існує → перенести recovery назад у `data_root`, видалити журнал →
     `action = "rolled_back"`.
   * `data_root` **існує** (крок A не відбувся) → лише видалити staging, видалити
     журнал → `action = "staging_discarded"`.
4. `stage == "swap_completed"`: дописати settings/dictionary з
   `data_root/.restore-settings.json`, якщо він є, видалити журнал →
   `action = "settings_completed"`.
5. **Ніколи** не видаляти каталог `*.recovery-*`. **Ніколи** не видаляти
   одночасно і staging, і recovery, якщо `data_root` ще не відновлено.
6. Функція має бути ідемпотентною: повторний виклик після успіху повертає
   `action = "none"`.

### 3.3. Точки виклику

Викликати `recover_interrupted_restore(data_dir(), settings_target=settings_path())`
**до першого відкриття сховища**:

* `src/voice_studio/app.py` → `VoiceStudioApp.__init__`, перед
  `self.store = LocalStore(data_dir())` (наразі рядок ~133). Результат показати
  користувачу: непорожній `action` → повідомлення у рядку статусу; `status == "FAIL"`
  → `messagebox.showwarning`. Виняток з функції не повинен ламати запуск застосунку.
* `src/voice_studio/cli.py` → перед обома викликами `LocalStore(data_dir())`
  (наразі рядки ~265 і ~276) і перед `restore_backup(...)`. У CLI результат
  друкувати як частину JSON або в `stderr`, залежно від команди; мовчазне
  ігнорування недопустиме.

### 3.4. Локалізація

Нові ключі UI додати **в усі три каталоги** `uk`, `cs`, `en` у
`src/voice_studio/i18n.py`. Модуль на імпорті кидає `RuntimeError`, якщо набори
ключів різні — набори мають лишитися ідентичними.

Мінімальний набір: `restore_recovered`, `restore_rolled_back`,
`restore_recovery_failed`.

---

## 4. RELEVANT AREAS

* `src/voice_studio/backup.py` — журнал, `recover_interrupted_restore`.
* `src/voice_studio/app.py` — виклик при старті, статус, i18n.
* `src/voice_studio/cli.py` — виклик при старті.
* `src/voice_studio/i18n.py` — нові ключі в трьох каталогах.
* `src/voice_studio/storage.py` — **тільки читання**, повторне використання `audit()`.
* `tests/test_backup_app.py` — основні тести.
* `tests/test_editor_state_app.py` — там уже є тести GUI-restore, за потреби доповнити.

---

## 5. CONSTRAINTS (що зберегти обов'язково)

* Ніколи не видаляти оригінал користувача. Каталог `*.recovery-*` не видаляється
  автоматично ніколи.
* `raw_text` лишається незмінним; відновлення не переписує текст транскриптів.
* `BACKUP_VERSION = 1` не змінювати. Формат архіву і контракт `verify_backup`
  не змінювати.
* Префлайт вільного місця, ZIP-бюджет, звірка кількості записів і `store.audit()`
  лишаються перед будь-якою підміною.
* Жодної нової runtime-залежності. Жодної мережі на цьому шляху.
* Журнал не містить секретів: ні ключів API, ні тексту транскриптів.
* Публічні сигнатури `verify_backup` / `restore_backup` не змінювати; лише
  **додаткові** ключі у поверненому словнику.
* Набори ключів усіх трьох каталогів i18n лишаються ідентичними.

---

## 6. DO NOT TOUCH (не чіпати без прямої необхідності)

* Гейтинг AI-редагування (Ollama/OpenAI) у `cli.py` та `app.py` — він щойно
  покритий регресійними тестами в `tests/test_cli_app.py`.
* `docs/help/`, `help_content.py`, вікно довідки.
* Тему, `studio_layout_for_width`, адаптивний розкладку, іконку.
* `src/voice_studio/media.py` (ізоляція розбору медіа).
* `packaging/voice_studio.spec`, `scripts/build_windows.ps1`,
  `requirements-windows.lock`.
* `.gitignore`, `scripts/quality_gate.sh`, `scripts/quality_gate.ps1`,
  `.github/workflows/*`.

---

## 7. ACCEPTANCE CRITERIA (перевірювані умови)

Кожен пункт має бути покритий тестом, який **падає без виправлення**:

1. **W1 — доведення до кінця.** Тест імітує загибель процесу між кроком A і
   кроком B (наприклад, патч `Path.replace`, який кидає `KeyboardInterrupt` рівно
   на другому виклику, при вже записаному журналі). Після цього
   `recover_interrupted_restore` повертає `action = "completed"`, `data_root`
   існує, `LocalStore(data_root).list()` містить рівно `expected_records`
   записів, `audit()["status"] == "PASS"`.
2. **W1 — відкат.** Той самий сценарій, але staging видалено/пошкоджено:
   `action = "rolled_back"`, у `data_root` — дані, що були **до** відновлення,
   каталог `*.recovery-*` існує на диску після відновлення.
3. **W2 — дозапис settings.** Процес «гине» після успішного кроку B, до запису
   `settings.json`: `action = "settings_completed"`, `settings.json` містить
   налаштування з архіву, копія `<name>.pre-restore-<timestamp>` не втрачена.
4. **W3 — прибирання staging.** Журнал є, `data_root` цілий:
   `action = "staging_discarded"`, staging видалено, `data_root` не змінено.
5. **Успішний шлях.** Після звичайного `restore_backup` журнал на диску
   відсутній, а `recover_interrupted_restore` повертає `action = "none"`.
6. **Ідемпотентність.** Другий поспіль виклик `recover_interrupted_restore`
   повертає `action = "none"` і нічого не змінює на диску.
7. **Пошкоджений журнал.** Журнал із невідомим `journal_version` або невалідним
   JSON → `status = "FAIL"`, жоден каталог не видалено.
8. **Без секретів.** Тест читає файл журналу і стверджує, що в ньому немає
   тексту транскриптів і немає рядка ключа API.
9. **Каталоги i18n.** `set(_CATALOGS["uk"]) == set(_CATALOGS["cs"]) == set(_CATALOGS["en"])`
   (перевіряється вже наявним контрактом на імпорті + тестом).
10. Публічні сигнатури `verify_backup` / `restore_backup` не змінені, наявні
    тести `tests/test_backup_app.py` проходять без правок їхніх очікувань.

---

## 8. VERIFICATION (що реально запустити і навести вивід)

```bash
python -m compileall -q src tests scripts packaging
python -m ruff check src tests scripts packaging
PYTHONPATH=src python scripts/check_help.py
PYTHONPATH=src python -m pytest -q
python -m build --wheel
python -m pip check
```

Плюс:

* `scripts/quality_gate.ps1` (Windows) або `scripts/quality_gate.sh` (macOS/Linux) —
  на тій машині, де ведеться робота;
* запуск GUI із джерел із чистим профілем (`VOICE_STUDIO_CONFIG_DIR`,
  `VOICE_STUDIO_DATA_DIR`, `VOICE_STUDIO_CACHE_DIR`) і підтвердження, що за
  відсутності журналу старт не змінився;
* RED/GREEN-докази: спочатку показати падіння нових тестів без реалізації, потім
  їх проходження.

Базовий стан для порівняння на `d61f1d9` (`main`): **320 passed**, `ruff` чистий,
wheel і `pip check` — PASS.

---

## 9. HANDOFF BACK (що повернути)

Коротко, без великого звіту:

```
CHANGED
- ...

VERIFIED
- ...

KNOWN ISSUES
- ...
```

Якщо якийсь пункт SCOPE виконати не вдалося — прямо написати який і чому,
замість того щоб мовчки звузити обсяг.
