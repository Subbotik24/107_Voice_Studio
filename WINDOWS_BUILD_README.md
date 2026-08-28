# Windows build copy

Ця копія призначена для Windows 10/11 x64. Вона не містить user data,
локальних моделей, `.venv`, macOS `.app/.dmg` або попередніх build‑артефактів.

## Найпростіший варіант

1. Розпакуйте ZIP у звичайний локальний каталог Windows.
2. Встановіть 64‑bit Python 3.12 з `python.org`.
3. Запустіть `build_windows_exe.bat`.
4. Скрипт створить чисте ізольоване `.venv-windows-build`, встановить точні
   версії з `requirements-windows.lock`, виконає lint/tests, збере `.exe`,
   перевірить frozen worker/runtime і короткий запуск GUI.
5. Готовий результат буде у
   `dist\0.3.0-test-rc1-windows-x64\`.

`run_windows.bat` запускає програму без попереднього створення `.exe`.
Зовнішній FFmpeg не потрібний: декодування підтримуваних форматів виконує PyAV.

Типовий профіль використовує окремо встановлену Ollama і встановлену
audio-capable модель для транскрипції та локального AI-редагування. VOICE Studio
автоматично покаже сумісні локальні моделі у вкладці **Налаштування**. Ollama й
ваги моделей не вбудовуються в `.exe`; без них виберіть явний профіль
**Локальний Whisper** або **OpenAI cloud**.

## Важливе обмеження

Windows `.exe` не можна коректно cross‑compile або перевірити через PyInstaller
на macOS. Поточний Test RC зібрано на реальному Windows 11 x64; runtime probe,
окремий worker, GUI smoke та реальна packaged-транскрипція через локальну
`gemma4:12b` пройшли.
Підпис коду, clean-machine перевірка, реальний мікрофон/hotkey і 50-task
acceptance залишаються окремими release-gate перевірками.
