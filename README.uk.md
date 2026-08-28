# VOICE Studio 0.3.0 Test RC

Локальна desktop-програма для транскрипції на macOS Apple Silicon і Windows x64.
За замовчуванням працює `faster-whisper`: аудіо, транскрипти, історія та моделі
залишаються на пристрої. Хмарні функції запускаються лише окремою явною дією.

Це **unsigned Test RC**, а не production release.

Повний посібник користувача: [docs/help/README.md](docs/help/README.md).

## Запуск

Потрібен Python 3.11 або 3.12.

```bash
# macOS: попередньо встановіть Python/Tk і PortAudio
./run_mac.command

# Windows 10/11 x64
run_windows.bat
```

На першому запуску створюється `.venv`; наступні запуски не оновлюють залежності.
Модель не завантажується без підтвердження:

```bash
voice-studio models install tiny
voice-studio models install small
voice-studio models install tiny --from-directory ШЛЯХ_ДО_МОДЕЛІ
```

`tiny` — стартовий профіль, `small` — профіль кращої якості. Для GitHub Release
моделей задайте URL `model-registry-v1.json` через `VOICE_STUDIO_MODEL_REGISTRY_URL`.
ZIP-моделі та ваги не комітяться в Git.

## Мова інтерфейсу та локальна Ollama

У вкладці **Налаштування** інтерфейс перемикається між українською, чеською та
англійською мовами. Зміна зберігається локально й застосовується одразу.

Локальна Ollama є стандартним провайдером AI-редагування. Якщо Ollama працює на
стандартній локальній адресі, поле **Локальна модель Ollama** показує моделі,
вже встановлені на цьому комп'ютері. Для цього не потрібні API key або згода на
передачу тексту в хмару. Список можна оновити в тому самому вікні або виконати:

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
