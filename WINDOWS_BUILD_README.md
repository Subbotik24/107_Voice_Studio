# Windows build copy

Ця копія призначена для Windows 10/11 x64. Вона не містить user data,
локальних моделей, `.venv`, macOS `.app/.dmg` або попередніх build‑артефактів.

## Найпростіший варіант

1. Розпакуйте ZIP у звичайний локальний каталог Windows.
2. Встановіть 64‑bit Python 3.11 або 3.12 з `python.org`.
3. Запустіть `build_windows_exe.bat`.
4. Скрипт створить ізольоване `.venv-windows-build`, виконає lint/tests,
   збере `.exe`, перевірить frozen runtime і короткий запуск GUI.
5. Готовий результат буде у
   `dist\0.3.0-test-rc1-windows-x64\`.

`run_windows.bat` запускає програму без попереднього створення `.exe`.
FFmpeg опційний для базового запуску та потрібен для Hermes‑конвертації
не-WAV медіа.

## Важливе обмеження

Windows `.exe` не можна коректно cross‑compile або перевірити через PyInstaller
на macOS. Тому цей source/build архів є робочою копією для побудови `.exe` на
реальному Windows x64, але не повинен називатися перевіреним Windows release,
доки там не пройдуть runtime probe, GUI smoke, microphone/hotkey permissions і
50-task acceptance.
