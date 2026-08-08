# Runbook навчання

## 1. Release-послідовність

1. Зібрати дозволений корпус.
2. Очистити дублікати та конфліктні транскрипції.
3. Поділити дані за speaker/source/document до будь-якого навчання.
4. Навчити токенізатор тільки на `train`.
5. Запустити `nano` як data-pipeline pilot.
6. Виправити системні помилки даних.
7. Зафіксувати незмінний test-набір.
8. Навчити `150m`.
9. Порівняти з обраним baseline на тих самих записах.
10. Лише після gate — квантизація та інтеграція у desktop-програму.

## 2. Дані до запуску

Кожний JSONL record має пройти:

- аудіофайл існує і читається;
- mono/стерео коректно зводиться до mono;
- тривалість додатна і не перевищує 30 с;
- текст відповідає саме цьому аудіо;
- мова — `uk` або `cs`;
- є `source`, `license`, `consent=true`;
- один speaker/document/source family не перетинає train і test;
- PII або чутливі матеріали не потрапляють у набір без окремої правової
  підстави.

Команди:

```bash
hermes-whisper validate-manifest data/train.jsonl --languages uk cs
hermes-whisper validate-manifest data/validation.jsonl --languages uk cs
hermes-whisper validate-manifest data/test.jsonl --languages uk cs
```

## 3. Токенізатор

Токенізатор створюється один раз перед model run:

```bash
hermes-whisper init-tokenizer \
  --manifest data/train.jsonl \
  --languages uk cs \
  --text-vocab-size 8192 \
  --min-pair-frequency 10 \
  --output artifacts/tokenizer.json
```

Після створення перевірити:

- round-trip українського і чеського тексту;
- наявність чисел, одиниць, апострофів і діакритики;
- середню та p95 довжину token sequence;
- records, що перевищують `max_text_tokens`;
- фактичний `text_vocab_size`.

Зміна токенізатора означає новий model run.

## 4. Nano pilot

```bash
hermes-whisper train \
  --config configs/hermes-whisper-nano.json \
  --tokenizer artifacts/tokenizer.json \
  --train-manifest data/train.jsonl \
  --validation-manifest data/validation.jsonl \
  --run-directory runs/nano-pilot
```

Pilot перевіряє:

- чи падають train/validation losses;
- чи не домінує одна мова;
- чи немає `NaN`, CTC overflow або порожніх batch;
- чи відновлюється checkpoint;
- які типові помилки WER/CER;
- які домени або мікрофони дають найбільшу помилку.

Не переходити до 150M, якщо data pipeline ще змінюється.

## 5. Multi-GPU

```bash
torchrun --standalone --nproc_per_node=4 -m hermes_whisper.cli train \
  --config configs/hermes-whisper-150m.json \
  --tokenizer artifacts/tokenizer.json \
  --train-manifest data/train.jsonl \
  --validation-manifest data/validation.jsonl \
  --run-directory runs/hermes-150m
```

Перед довгим запуском зробити короткий run на тому самому сервері та виміряти:

- peak VRAM;
- samples/s і audio-hours/s;
- CPU data-loader utilization;
- checkpoint size та час запису;
- стабільність `bf16`;
- ефективний batch:

```text
effective batch =
  batch_size × gradient_accumulation_steps × world_size
```

Конфігурації в репозиторії є стартовими. Вони не гарантують сумісність із
конкретною VRAM.

## 6. Відновлення

```bash
hermes-whisper train \
  --config runs/hermes-150m/config.json \
  --tokenizer runs/hermes-150m/tokenizer.json \
  --train-manifest data/train.jsonl \
  --validation-manifest data/validation.jsonl \
  --run-directory runs/hermes-150m \
  --resume runs/hermes-150m
```

Resume відхиляється, якщо fingerprint конфігурації або розмір токенізатора не
збігається. Manifest fingerprint зберігається у checkpoint metadata.

## 7. Benchmark

```bash
hermes-whisper evaluate \
  --checkpoint runs/hermes-150m \
  --manifest data/test.jsonl \
  --device cuda \
  --predictions runs/hermes-150m/test-predictions.jsonl
```

Звіт треба розкласти щонайменше за:

- `uk` / `cs`;
- чисте / шумне аудіо;
- мікрофон;
- speaker;
- загальна / професійна лексика;
- коротка / довга фраза.

Головні метрики:

- corpus WER;
- corpus CER;
- real-time factor;
- language identification accuracy;
- термінологічна точність;
- hallucination/empty-output rate.

## 8. Promotion gate

Ваги не переводяться у desktop-версію, доки:

- test-набір не відокремлений від train/validation;
- немає speaker/source leakage;
- Hermes кращий за зафіксований baseline хоча б за обраним цільовим критерієм;
- quality не деградує критично на другій мові;
- checksum checkpoint проходить;
- license/consent audit завершений;
- відомі обмеження внесені в `MODEL_CARD.md`.

Числові пороги WER/CER треба встановити після першого baseline на реальних
даних. До цього конкретний поріг був би вигаданим.
