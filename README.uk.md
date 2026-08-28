# VOICE Studio 0.3.0 Test RC

Локальна desktop-програма для транскрипції на macOS Apple Silicon і Windows x64.
За замовчуванням працює локальна Ollama: вибрана встановлена audio-capable модель
розпізнає аудіо й локально виправляє редагований текст. Аудіо, транскрипти,
історія та налаштування залишаються на пристрої. Хмарні функції запускаються
лише після вибору окремого профілю OpenAI та явної згоди.

Це **unsigned Test RC**, а не production release.

Посібник: [Українська](docs/help/uk/quick-start.md) ·
[Čeština](docs/help/cs/quick-start.md) · [English](docs/help/en/quick-start.md).

## Запуск

Потрібен Python 3.11 або 3.12.

```bash
# macOS: попередньо встановіть Python/Tk і PortAudio
./run_mac.command

# Windows 10/11 x64
run_windows.bat
```

На першому запуску створюється `.venv`; наступні запуски не оновлюють залежності.
Програма не показує примусове вікно початкового налаштування AI. Запустіть Ollama
й виберіть одну з уже встановлених моделей із підтримкою аудіо:

```bash
ollama list
voice-studio transcribe recording.wav --engine ollama --ollama-model gemma4:12b
```

Whisper є окремим необов'язковим локальним профілем для точних часових сегментів:

```bash
voice-studio models install tiny
voice-studio models install small
voice-studio models install tiny --from-directory ШЛЯХ_ДО_МОДЕЛІ
```

`tiny` — компактна Whisper-модель, `small` — профіль кращої якості. Для GitHub Release
моделей задайте URL `model-registry-v1.json` через `VOICE_STUDIO_MODEL_REGISTRY_URL`.
ZIP-моделі та ваги не комітяться в Git.

## Збережені профілі, мова інтерфейсу та локальна Ollama

У **Налаштування → Профілі** доступні **Локальна Ollama** (типовий),
**Локальний Whisper** і **OpenAI cloud**. Вибраний профіль, модель та інші
параметри зберігаються локально й відновлюються після наступного запуску.

Інтерфейс та вбудована Довідка разом перемикаються між українською, чеською та
англійською. Якщо Ollama працює на стандартній loopback-адресі, поле
**Локальна модель Ollama** показує встановлені моделі з підтримкою аудіо. Для
цього не потрібні API key або згода на передачу даних у хмару.

У типовому профілі Ollama безпосередньо розпізнає мовлення та автоматично
покращує текст локально. Незмінний STT-результат залишається в `raw_text`, а
обробка змінює лише `corrected_text`. Якщо обробка не вдалася, транскрипт усе
одно зберігається, а програма показує зрозуміле попередження. Один Ollama-запит
обмежений 30 хвилинами; для довших файлів використовуйте профіль Whisper.

Окреме редагування також доступне з CLI:

```bash
voice-studio cleanup TRANSCRIPT_ID --provider ollama --model НАЗВА_МОДЕЛІ
```

VOICE Studio не встановлює, не оновлює та не видаляє моделі Ollama. Якщо модель
не завантажується, програма показує помилку локального runtime, щоб можна було
вибрати іншу встановлену модель.

## OpenAI — лише за згодою

Ключ береться з `OPENAI_API_KEY` або macOS Keychain / Windows Credential Manager:

```bash
voice-studio cloud key set
voice-studio cloud key status
```

Аудіо не завантажується без `--allow-cloud-upload`; ліміт — 25 MB, без тихого
стискання або поділу файлу:

```bash
voice-studio transcribe recording.m4a --engine openai-cloud --allow-cloud-upload
```

AI cleanup надсилає лише `corrected_text`, а не незмінний `raw_text`; спочатку
друкує proposal, а збереження потребує `--apply`:

```bash
voice-studio cleanup TRANSCRIPT_ID --provider openai --allow-cloud-text
voice-studio cleanup TRANSCRIPT_ID --provider openai --allow-cloud-text --apply
voice-studio cleanup-undo TRANSCRIPT_ID
```

`offline_only` повністю блокує cloud STT і cleanup. Ключ, аудіо, повна відповідь
провайдера та user paths не потрапляють у settings, backup, diagnostics чи Git.

## Test RC

Очікувані assets: unsigned `.dmg` для macOS ARM64 і ZIP для Windows x64, wheel,
manifest і `SHA256SUMS.txt`. Gatekeeper/SmartScreen warnings очікувані через
відсутність підпису. Завантажуйте лише з GitHub Release, звіряйте SHA-256 і не
вважайте RC production-ready.

Для форумного звіту: `voice-studio diagnostics --export report.json`. Не
публікуйте приватне аудіо, транскрипти, API keys, бази даних чи повні user paths.

Повний англомовний опис: [README.md](README.md).
