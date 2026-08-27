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

Для локального AI-редагування можна окремо встановити Ollama. VOICE Studio
автоматично покаже її локальні моделі у вкладці **Налаштування**; Ollama не
вбудовується в `.exe` і не є обов'язковою для транскрипції.

## Важливе обмеження

Windows `.exe` не можна коректно cross‑compile або перевірити через PyInstaller
на macOS. Поточний Test RC зібрано на реальному Windows 11 x64; runtime probe,
окремий worker із реальною `faster-whisper` моделлю та GUI smoke пройшли.
Підпис коду, clean-machine перевірка, реальний мікрофон/hotkey і 50-task
acceptance залишаються окремими release-gate перевірками.
