# VOICE Studio — потенційний розвиток R1/R2

Цей файл описує можливе розширення продукту після завершення R0. Пункти нижче
не входять у поточний обсяг реалізації, не є обіцянкою релізу й не повинні
ускладнювати архітектуру R0 наперед.

## R1 — розвиток настільного продукту

R1 поглиблює вже наявні сценарії транскрибування та редагування.

### Speaker diarization

Визначення різних голосів і позначення `Speaker 1`, `Speaker 2` тощо.

- Користь: інтерв’ю, наради, подкасти.
- Складність: окрема локальна модель, більше RAM/VRAM, зіставлення голосів із
  часовими сегментами, нові формати експорту.
- Умова старту: окремий benchmark і privacy review для моделі diarization.

### Word timestamps

Час початку й завершення для кожного слова.

- Користь: точні субтитри, пошук по аудіо, майбутнє караоке-підсвічування.
- Складність: зміна schema `Segment`, backup format і всіх exporters; Ollama не
  гарантує такі дані, тому функція буде engine-dependent.
- Умова старту: завершена R0-синхронізація сегментів і versioned migration.

### Повний редактор сегментів

Ручні split, merge, retime, прослуховування фрагмента й корекція меж.

- Користь: професійна підготовка SRT/VTT.
- Складність: новий timeline UI, undo/redo, валідація неперетинних інтервалів.
- Межа з R0: R0 лише зберігає узгодженість наявних сегментів і не вигадує час.

### Batch transcription

Черга багатьох файлів із повторним використанням моделі.

- Користь: архіви лекцій, інтерв’ю, медіатеки.
- Складність: scheduler, disk/RAM budgets, per-item retry, aggregate progress,
  відновлення черги після перезапуску.

### Проєктні словники

Окремі набори термінів для клієнтів або тематик.

- Користь: точні назви, абревіатури та професійна лексика.
- Складність: versioned profiles, import/export, конфлікти правил і provenance.

### Вставлення в активну програму

Явна команда вставляє результат у поточний редактор або чат.

- Користь: швидкий dictation workflow.
- Ризик: помилкове вставлення приватного тексту, Accessibility permissions,
  clipboard/keyboard injection.
- Умова старту: окремий security review і opt-in за замовчуванням `false`.

### Local REST/IPC adapter

Локальний програмний інтерфейс для інших desktop-застосунків.

- Користь: інтеграції з редакторами, automation tools і локальними агентами.
- Ризик: authentication, origin/process boundary, витік transcript data.
- Умова старту: threat model і loopback/IPC access-control design.

## R2 — платформне розширення

R2 перетворює VOICE Studio з окремого desktop-інструмента на ширшу локальну
аудіоплатформу.

### System audio capture

Запис звуку операційної системи, дзвінків або відтворюваного медіа.

- Складність: різні API Windows/macOS, permissions, echo/feedback і юридичні
  правила запису розмов.

### Live streaming transcription

Часткові результати під час розмови, а не після завершення файла.

- Складність: streaming decoder, стабілізація partial text, backpressure,
  перерозподіл сегментів і новий UI стану.

### Підписаний канал оновлення моделей

Перевірені manifest і model assets із контрольованим rollout/rollback.

- Складність: publisher signing keys, rotation/revocation, offline trust root,
  release infrastructure і incident response.
- SHA-256 без цифрового підпису для цього недостатньо.

### Team export/import без cloud dependency

Передавання словників, профілів і вибраних transcript packages між командами.

- Складність: access policy, redaction, encryption, conflict resolution,
  provenance і сумісність schema.

## Принцип прийняття R1/R2

Кожна майбутня функція потребує окремого рішення власника продукту, design spec,
оцінки privacy/security, вимірюваних acceptance criteria і доказу, що вона не
порушує локальний/private-by-default контракт. До такого рішення R0 не створює
прихованих сервісів, telemetry, cloud synchronization або нестабільних API “про
запас”.
