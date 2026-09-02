# Roadmap

Current Test RC status and verified evidence are tracked in
`IMPLEMENTATION_STATUS.md` and `VERIFICATION.md`.
Potential post-R0 growth is described separately in `FUTURE_GROWTH.md`; those
R1/R2 capabilities are not part of the current completion scope.

## P0 — довести desktop‑застосунок до release candidate

1. **Реальний E2E на macOS та Windows**
   - мікрофон, hotkey, WAV/MP3/M4A/MP4;
   - uk/cs/en/auto;
   - keep/delete retention;
   - export усіх форматів;
   - критерій: 0 crash у контрольній серії не менше 50 задач на ОС.
   - стан: macOS 50-task PASS із task-level JSON і незмінними originals;
     manual Accessibility delivery та Windows 50-task/permissions TODO.

2. **Керування довгими операціями**
   - cancellation token;
   - progress/status для завантаження моделі та транскрибування;
   - timeout і коректне відновлення UI;
   - критерій: користувач може скасувати задачу без завершення програми.
   - стан: реалізовано persistent spawn worker, Cancel, timeout і progress.

3. **Model management**
   - локальний каталог моделей;
   - явний download/install/remove;
   - offline‑only режим;
   - перевірка вільного місця та зрозумілі помилки.
   - стан: реалізовано catalog/install/import/verify/remove/offline.

4. **Packaging**
   - PyInstaller/Nuitka pipeline;
   - `.app/.dmg` для Apple Silicon та `.exe/.msi` для Windows x64;
   - code signing/notarization;
   - automated clean‑machine install test.
   - стан: locked Python 3.12 Windows x64 pipeline, atomic build, frozen-worker
     runtime probe, unsigned `.exe` і portable ZIP перевірено; macOS `.dmg`,
     clean-machine acceptance і signing/notarization ще TODO.

5. **Storage migrations та backup**
   - versioned schema;
   - export/import backup;
   - recovery test після пошкодження DB.
   - стан: schema, audit, backup verify, reversible restore і encrypted backup v2
     (opt-in `--encrypt`, Argon2id → HKDF → AES-256-GCM, sidecar/journal recovery,
     CLI/GUI prompts) реалізовано source/headless; packaged/native acceptance —
     R0.10.

6. **Benchmark**
   - фіксований набір uk/cs/en записів;
   - WER/CER/RTF/RAM/model load time;
   - профілі tiny/base/small/medium/large-v3;
   - окремі результати для Mac mini M4 і Windows target.
   - стан: harness і synthetic smoke готові; licensed corpus та production results відсутні.

7. **Persistent engine profiles**
   - `Локальна Ollama` як приватний профіль за замовчуванням;
   - `Локальний Whisper` і `OpenAI cloud` як явні альтернативи;
   - збереження точного engine/model/language selection без startup wizard;
   - стан: реалізовано й перевірено у фінальному Windows EXE.

8. **Localized in-app Help**
   - один canonical source у `docs/help/{uk,cs,en}`;
   - Help автоматично відповідає мові UI;
   - packaged assets і links перевіряються build gate;
   - стан: реалізовано; український і чеський Help візуально перевірено.

9. **Hardware settings validation and advisory detection (W3-H1)**
   - статичні `device`/`compute_type` choices і runtime preflight;
   - bounded local detection без model load, network або silent mutation;
   - стан: реалізовано source/headless; packaged console probe round-trip
     отримав capabilities PASS на холодному child без parent preload (2.858 s
     CTranslate2 import, bounded 5 s deadline); normal windowed bundle CLI
     dispatch remains a separate launcher limitation.

10. **Recognition and subtitle controls (W3-V1, W3-E1)**
   - VAD можна явно ввімкнути або вимкнути; сумісний default лишається enabled;
   - ручні правки синхронізуються з наявними subtitle intervals без вигаданих
     timecode; TXT/MD і SRT/VTT використовують один виправлений текстовий шар;
   - стан: реалізовано й перевірено source/headless; повний split/retime editor
     та word timestamps лишаються поза R0.

11. **Робочий простір і локальні інструменти редактора (usability pack)**
   - центральна навігація Огляд/Студія/Словник/Історія з dirty-guard;
   - керований словник термінів і per-request hotwords для локального Whisper;
   - статистика Огляду по всій історії та комбіновані фільтри Історії до ліміту;
   - пошук/заміна, додавання виділення до словника, чистка слів-паразитів;
   - панель впевненості: поріг лише як стан сторінки, без заяв про точність;
   - локальне відтворення лише збереженої керованої копії аудіо;
   - стан: реалізовано й перевірено source/headless на Linux
     (`1135 passed`, 14 junction skips, 2026-09-01); packaged/native acceptance,
     фізичний Tk smoke і відтворення на реальному аудіопристрої — NOT RUN.

12. **Черга, Розумний текст, папка синхронізації (feature pack 2026-09)**
   - черга файлів у пам'яті на тому самому job path, без паралельних задач;
   - абзаци за паузами, часові мітки, ручні мітки спікерів у metadata;
   - локальне дзеркало у папку користувача з валідацією кореня при збереженні
     та перед кожним записом; нічого не видаляє, ключів і `source_path` не пише;
   - стан: реалізовано й перевірено source/headless (`1379 passed`, R0.11
     фікси 2026-09-02); рішенням власника включено до R0 як amendment до
     `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md`;
     реальний cloud-клієнт, реальний движок у черзі й native acceptance — NOT RUN.

Повний поетапний план до кінцевого стану, включно з протоколами native
acceptance, `models-v1`, підпису та go/no-go:
`docs/superpowers/plans/2026-09-02-completion-plan.md`.

Наступні кроки P0: native/packaged acceptance на Windows 10/11 x64 і macOS
Apple Silicon (пункти 1 і 4), release assets `models-v1` через
`scripts/build_model_release.py`, далі signing/notarization.

## P1

- speaker diarization;
- word timestamps;
- повний segment editor із split/retime і ручною корекцією часових меж;
- відновлення черги після перезапуску, scheduler/budgets і per-item retry
  (сама черга в пам'яті вже в R0);
- profiles словників за проєктами;
- автоматичне вставлення в active app після окремого security review;
- локальний REST/IPC adapter лише після окремого security review.

## P2

- system audio capture;
- live streaming encoder;
- model update channel із підписаним manifest;
- team export/import без cloud dependency.
