# ЗАВДАННЯ ДЛЯ ANTIGRAVITY — COMPLETE

**TASK_ID:** W2-C2-model-catalog-self-healing
**СТАТУС:** `COMPLETE` — цей запис закриває лише W2-C2, а не всі R0 або продуктову
приймальну програму.
**ГІЛКА РОБОТИ:** `main`. Нових гілок **не створювати** — уся розробка й пуші
йдуть безпосередньо в `main`.
**BASELINE:** `356d4f3` (`main`) — останній кодовий commit W2-C2 перед
документуванням і фінальною перевіркою.
**Останній перевірений зелений стан:** джерельний quality gate на Windows x64
з CPython 3.12: `426 passed`, `1 skipped` (Windows symlink privilege), Ruff,
Help validation, wheel і `pip check` — PASS; точні команди й обмеження наведені
у `VERIFICATION.md`. Completion commit має повідомлення
`docs: record model catalog self-healing verification`.
**Джерело завдання:** `IMPLEMENTATION_STATUS.md` → розділ «Not implemented yet» → пункт
«SQLite/filesystem/model-catalog reconciliation», половина про **каталог моделей**.
Нової функціональності не вигадувати.
**Контекст:** `docs/superpowers/plans/2026-08-28-completion-roadmap.md`, розділ W2-C2.

---

## 1. МЕТА

Зробити так, щоб **жоден стан каталогу моделей не був таким, який публічний API
відмовляється полагодити**. Смерть процесу під час встановлення або запису маніфесту
має лікуватися детерміновано при наступному запуску, без втрати завантажених моделей.

Одна мета. Нічого більше в цьому етапі не реалізовувати.

---

## 2. ПОТОЧНИЙ СТАН (що вже є в коді)

Файл: `src/voice_studio/model_catalog.py`.

* `__init__` (`:63-69`) — `self.root`, `self.downloads = self.root / ".downloads"`,
  `self.catalog_path = self.root / "catalog.json"`.
* `CATALOG_VERSION = 1` (`:24`), `MODEL_ID_PATTERN = ^[A-Za-z0-9][A-Za-z0-9._-]*$` (`:25`).
* `_load` (`:78-87`) — читає **лише** маніфест, кидає `ValueError` на пошкодженні
  або несумісній версії.
* `_save` (`:89-95`) — пише `catalog.json.tmp`, потім `replace`. **Без `finally: unlink`**
  і зі **спільним** іменем tmp-файла.
* `list()` (`:97-98`) — повертає записи маніфесту, **файлової системи не торкається**.
* `resolve()` (`:104-116`) — єдина звірка з ФС, лінива й для однієї моделі.
* `_inventory` (`:137-147`) — SHA-256 кожного файла + розмір; вимагає `model.bin`
  і `config.json`.
* `_promote` (`:149-176`) — `_remove_transient_files` → `_inventory` →
  `if target.exists(): raise FileExistsError` → **`temporary.replace(target)` (`:162`)**
  → побудова запису → `_load`/append/**`_save` (`:175`)**.
* `install()` (`:214-291`) — `_validate_model_id`, `if offline_only: raise` (`:226-227`),
  **`if self.get(value): raise FileExistsError` (`:228-229`)**, далі release-registry
  або HF-завантаження в spawn-дитині; `finally` прибирає staging (`:286-291`).
* `remove()` (`:338-355`) — вимагає `confirmed`,
  **`entry = self.get(value); if entry is None: raise FileNotFoundError` (`:342-344`)**,
  перевірка вкладеності (`:346-349`), `rmtree`, видалення запису, `_save`.

### Підтверджені прогалини

**Прогалина 1 — осиротілий каталог.** Крах між `:162` і `:175` лишає каталог моделі
на диску **без запису в маніфесті**. Далі:

* `install(id)` проходить гард `:228-229` (запису немає) → качає повторно →
  падає на `:160-161` з `FileExistsError: model is already installed`;
* `remove(id)` відмовляє на `:342-344` з `FileNotFoundError: model is not installed`.

Стан **недосяжний для жодного публічного API**.

**Прогалина 2 — пошкоджений маніфест.** `_load` кидає `ValueError`, а через нього
проходять **усі** публічні методи. Обірваний `_save` одночасно ламає
`list`/`get`/`resolve`/`install`/`verify`/`remove`. Той самий клас дефекту.

**Прогалина 3 — сміття.** `catalog.json.tmp` лишається після невдалого `_save`
(немає `finally`), а спільне ім'я — це ще й гонка між двома процесами.
`.downloads/{id}-{hex}` накопичується, бо `finally` не виконується при SIGKILL.

### Що вже працює і має лишитися без змін

* `offline_only` перевіряється **першим** в `install` (`:226-227`) — пінить
  `tests/test_model_catalog_app.py:58-61`.
* `_inventory` як валідатор повноти моделі.
* Перевірка вкладеності в `remove` (`:346-349`).
* `verify()` (`:328-336`) як єдиний власник перевірки цілісності.

---

## 3. SCOPE — що саме реалізувати

### 3.1. Публічний метод примирення

```python
def reconcile(self) -> dict[str, Any]:
```

Повертає щонайменше `{"status", "action", "adopted", "dropped", "blocked",
"staging_removed", "staging_kept", "residue_removed", "catalog_quarantined"}`,
де `action ∈ {"none", "repaired", "attention"}`, а `status ∈ {"PASS", "FAIL"}`
(при `FAIL` — додатково `"error"`).

Алгоритм — детермінований, без інтерактиву, ідемпотентний, **офлайн**:

1. **Тимчасові файли маніфесту.** Прибрати `catalog.json.tmp` і `catalog.json.*.tmp`
   **старші за 300 с**. Свіжий може належати `_save` іншого процесу — не можна
   вирвати файл з-під його `replace`.
2. **Завантаження або карантин.** Якщо `_load()` кидає `ValueError`:
   перейменувати `catalog.json` у `catalog.json.corrupt-<ISO8601>-<hex8>` через `replace`
   і **відбудувати** маніфест із каталогів на диску (крок 4).
   Пошкоджений файл **ніколи не видаляється** — те саме правило, що для `*.recovery-*`.
   Якщо не вдалося навіть перейменувати → `status="FAIL"`, нічого не чіпати.
3. **Фантомні записи.** Для кожного запису визначити стан шляху **трибічно**, а не
   голим `is_dir()`:
   * `present` — каталог існує і не симлінк;
   * `absent` — `FileNotFoundError` або `NotADirectoryError`;
   * `unknown` — будь-який інший `OSError`, або шлях зайнятий файлом.

   Прибирати запис **лише** при `absent`. При `unknown` запис лишається, шлях іде
   в `blocked`. Це захист від масового хибного видалення записів при відмонтованому томі.
4. **Осиротілі каталоги.** Обхід `self.root` **на глибину 1** через `scandir`,
   **тільки каталоги**. Пропускати: `self.downloads`, симлінки, імена вже в маніфесті,
   імена, що не задовольняють `MODEL_ID_PATTERN`.
   * проходить `_inventory` → **всиновити**: запис із `source="reconciled"`,
     `revision=None`, `installed_at` з `st_mtime` каталогу, свіжими `files`/`size`,
     плюс явними `reconciled=True` і `reconciled_at`;
   * не проходить (`ValueError` або `OSError`) → у `blocked`, **не чіпати**.

   `_remove_transient_files` тут **не викликати**: він видаляє файли, а шлях репарації
   не має мутувати каталог, який йому ще не належить.
5. **Staging.** `.downloads/{id}-{32hex}` видаляти лише коли виконано **все**:
   ім'я відповідає шаблону `^[A-Za-z0-9][A-Za-z0-9._-]*-[0-9a-f]{32}$`;
   це справжній каталог, не симлінк; найновіший `mtime` у піддереві старший
   за 172 800 с (подвоєна стеля `task_timeout_seconds`, `models.py:152`).
   Усе інше — у `staging_kept`, **без** ескалації `action`: активне завантаження
   в іншому процесі — це норма, а не дефект.
6. **Один запис.** `_save` викликати **тільки** якщо щось справді змінилося.

**Вимога дешевизни.** На здоровому профілі — один `scandir` кореня і один `lstat`
на запис. SHA-256 рахувати **лише** для каталогів, яких немає в маніфесті. Інакше
кожен старт хешував би гігабайти.

### 3.2. Супутні виправлення (без них мета не досягається)

* **`_save` (`:89-95`)** → унікальне ім'я тимчасового файла
  (`catalog.json.<uuid4hex>.tmp`) + `finally: unlink(missing_ok=True)`,
  як `backup._write_json_atomic` (`backup.py:216-226`).
* **`remove()`** → додатково приймати каталог **без** запису в маніфесті.
  Сигнатура не змінюється, `confirmed=True` лишається обов'язковим, перевірка
  вкладеності та сама; у відповідь додається ключ `"unmanaged": True`.
  Без цього те, що `reconcile` відмовився всиновити (неповний каталог), лишається
  невидаляним — і мета «немає стану, який API відмовляється полагодити» не досягається.

### 3.3. Точки виклику

* `src/voice_studio/app.py` — у `VoiceStudioApp.__init__` **одразу після**
  `self._report_restore_recovery()`, тобто після `_build_ui` (рядок стану й
  `messagebox` уже існують) і **до** `self.after(400, self._first_run_model_prompt)`.
  Оскільки `after`-колбек не може виконатися, доки `__init__` не повернувся,
  `ModelCatalog(...).list()` на `app.py:224` гарантовано правдивий, коли спрацює
  запит першого запуску. Метод назвати `_settle_model_catalog` (**не** так, щоб він
  затінював `_settle_interrupted_restore` при пошуку підрядка). Виняток **не має**
  ламати запуск.
* `src/voice_studio/cli.py` — **тільки** всередині гілки
  `if args.command == "models":`, одразу після `catalog = ModelCatalog(store.models)`.
  **Не** після загального `LocalStore(data_dir())`: інакше сканування каталогу
  і потенційно багатогігабайтне хешування лягло б на `history`, `show`, `export`,
  `transcribe`.
* Нова підкоманда `models reconcile` — вивід JSON у **stdout**.
  Автоматичний виклик — повідомлення у **stderr**, лише коли він нетривіальний
  (`status != "PASS"` або `action != "none"`), за зразком `restore-journal:`.
* `src/voice_studio/diagnostics.py` **не чіпати**: діагностика лишається read-only.

### 3.4. Локалізація

Нові ключі додати **в усі три** каталоги `uk`, `cs`, `en` у `src/voice_studio/i18n.py`.
Модуль на імпорті кидає `RuntimeError`, якщо набори ключів різні.

Мінімальний набір: `model_catalog_repaired` (`{adopted}`, `{dropped}`),
`model_catalog_attention` (`{details}`), `model_catalog_rebuilt` (`{path}`),
`model_catalog_repair_failed` (`{error}`).

---

## 4. RELEVANT AREAS

* `src/voice_studio/model_catalog.py` — `reconcile`, `_save`, `remove`.
* `src/voice_studio/app.py` — виклик при старті, рядок стану, i18n.
* `src/voice_studio/cli.py` — виклик у гілці `models`, підкоманда `reconcile`.
* `src/voice_studio/i18n.py` — нові ключі в трьох каталогах.
* `src/voice_studio/backup.py` — **тільки читання**, як зразок
  (`recover_interrupted_restore`, `_write_json_atomic`).
* `tests/test_model_catalog_app.py` — основні тести.
* `tests/test_gui_contract_app.py`, `tests/test_cli_app.py` — тести точок виклику.
* `tests/test_i18n_app.py` — контракт каталогів.

---

## 5. CONSTRAINTS (що зберегти обов'язково)

* Каталог моделі користувача **не видаляти** без `confirmed=True`.
* Пошкоджений `catalog.json` **не видаляти ніколи** — лише в карантин.
* Жодної нової runtime-залежності. Жодної мережі на цьому шляху.
* `offline_only` не порушувати; його перевірка лишається першою в `install`.
* `CATALOG_VERSION = 1` не змінювати.
* Ваги моделей не потрапляють у Git.
* Публічні сигнатури `list` / `get` / `resolve` / `install` / `import_local` /
  `verify` / `remove` не змінювати; лише **додаткові** ключі у поверненому словнику.
* `reconcile()` **не викликати** зсередини `install` / `remove` / `get` / `resolve`.
* Набори ключів усіх трьох каталогів i18n лишаються ідентичними.

---

## 6. DO NOT TOUCH (не чіпати без прямої необхідності)

* `src/voice_studio/backup.py` і журнал відновлення — щойно покриті регресійними тестами.
* `src/voice_studio/diagnostics.py` — має лишитися read-only.
* Гейтинг AI-редагування (Ollama/OpenAI) у `cli.py` та `app.py`.
* Тему, `studio_layout_for_width`, адаптивну розкладку, іконку.
* `src/voice_studio/media.py`.
* `packaging/voice_studio.spec`, `scripts/build_windows.ps1`, `scripts/build_test_rc.sh`,
  `requirements-windows.lock`.
* `.gitignore`, `scripts/quality_gate.sh`, `scripts/quality_gate.ps1`,
  `.github/workflows/*`.

---

## 7. ACCEPTANCE CRITERIA (перевірювані умови)

Кожен пункт має бути покритий тестом, який **падає без виправлення**.

1. **Всиновлення.** Каталог на диску, запису в маніфесті немає (симулювати крах
   монкіпатчем `_save`, що кидає) → `adopted == [id]`, `get(id)` не `None`,
   `verify(id)["status"] == "PASS"`, `resolve(id)` повертає керований каталог.
2. **Стан перестав бути недосяжним.** Той самий стан:
   `remove(id, confirmed=True)` **до** примирення кидає `FileNotFoundError`,
   **після** — повертає `{"removed": True}` і каталог зникає з диска.
3. **Походження не вигадується.** Всиновлений запис несе `source="reconciled"`,
   `revision is None`, `reconciled is True`; каталог-джерело імпорту не змінено.
4. **Фантомний запис.** Запис у маніфесті, каталог видалено → `dropped` містить id,
   `list() == []`.
5. **Неповний каталог.** Каталог без `model.bin` → `action == "attention"`,
   один `blocked` зі згадкою `model.bin`, каталог **не всиновлено, не переміщено,
   не видалено** і він лишається на диску.
6. **Симлінк.** Каталог-симлінк → `blocked` із причиною `symlink`, не всиновлено,
   симлінк цілий.
7. **Staging.** Старий `.downloads/{id}-{32hex}` (mtime 3 дні тому) прибрано;
   свіжий і той, чиє ім'я не відповідає шаблону, лишаються і потрапляють
   у `staging_kept`.
8. **Тимчасові файли.** Старий `catalog.json.tmp` прибрано; свіжий
   `catalog.json.<hex>.tmp` лишається; `catalog.json` побайтово не змінений.
9. **Пошкоджений маніфест.** Невалідний JSON → `action == "repaired"`,
   `catalog_quarantined` існує на диску з пошкодженими байтами, `list()` повертає
   всиновлений запис, `verify()` PASS.
10. **Несумісна версія.** `{"version": 999, "models": []}` → те саме, що (9).
11. **Чистий профіль — без запису.** `action == "none"`, усі списки порожні,
    `_save` **не викликається** (перевіряється монкіпатчем `_save`, що кидає).
12. **Ідемпотентність.** Другий `reconcile()` → `action == "none"`, `list()` ідентичний,
    `st_mtime_ns` файла `catalog.json` не змінився.
13. **Дешевизна.** `reconcile()` не хешує файли моделей, які вже в маніфесті
    (лічильник викликів `sha256_file`).
14. **`remove` для некерованого каталогу.** Повний каталог без запису:
    `remove("stray")` → `ValueError("--yes")`; з `confirmed=True` →
    `{"removed": True, "unmanaged": True}` і каталог зникає.
15. **`_save` не лишає сміття.** Монкіпатч `Path.replace`, що падає лише для `*.tmp`
    → `list(root.glob("catalog.json*.tmp")) == []`.
16. **Хук обмежений гілкою `models`.** CLI `history` над тим самим станом
    **не** друкує `model-catalog:` у stderr; `models list` — друкує.
17. **Каталоги i18n.** `set(_CATALOGS["uk"]) == set(_CATALOGS["cs"]) == set(_CATALOGS["en"])`.
18. **Сигнатури не змінені.** Наявні 5 тестів `tests/test_model_catalog_app.py`
    проходять **без правок їхніх очікувань**.

### Пастки з наявними тестами (перевірено читанням)

* `tests/test_takeover_regressions.py:50-56` кладе `model.bin` і `config.json`
  **прямо в `catalog.root`**. Обхід через `rglob` або врахування вільних файлів
  перетворив би це на фантомну «модель» і зламав би тест. Тому: `scandir` на глибину 1,
  тільки `entry.is_dir()`, без симлінків.
* `tests/test_cli_app.py:178-186` («чистий старт без шуму») зламається, якщо хук
  поставити після `LocalStore(data_dir())` у `cli.py`.
* `tests/test_gui_contract_app.py:292-305` використовує `source.index(...)`, який
  знаходить **перше** входження — новий виклик має називатися відмінно, щоб не
  затінити `_settle_interrupted_restore`.
* `tests/test_model_catalog_app.py:58-61` вимагає, щоб перевірка `offline_only`
  лишалася першою в `install`.

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
  `VOICE_STUDIO_DATA_DIR`, `VOICE_STUDIO_CACHE_DIR`) і підтвердження, що за відсутності
  розходжень старт не змінився;
* RED/GREEN-докази: спочатку показати падіння нових тестів без реалізації, потім
  їх проходження.

Базовий стан для порівняння на `e22a65f` (`main`): **347 passed**, `ruff` чистий,
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
