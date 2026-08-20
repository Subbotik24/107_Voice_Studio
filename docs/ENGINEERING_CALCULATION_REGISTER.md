# 107 Voice Studio — engineering calculation register

## Scope and status vocabulary

This product is not a regulated physical calculation engine, but Hermes Whisper and desktop reporting contain DSP, ML, metric and resource calculations that can materially alter transcript quality and release claims.

Statuses: `VERIFIED`, `PARTIALLY VERIFIED`, `FAIL`, `NOT VERIFIED`, `REQUIRES DOMAIN EXPERT`, `REQUIRES AUTHORITATIVE SOURCE`.

Engineering severity: `ENG-CRITICAL`, `ENG-HIGH`, `ENG-MEDIUM`, `ENG-LOW`.

## Register

| ID | Module / purpose | Formula | Inputs / units | Output / units | Source | Verification status | Risk |
|---|---|---|---|---|---|---|---|
| CALC-001 | `src/hermes_whisper/audio.py`; Hz↔mel | `m=2595 log10(1+f/700)`; `f=700(10^(m/2595)-1)` | frequency Hz; mel dimensionless | mel / Hz | formula is conventional; no project citation | PARTIALLY VERIFIED | ENG-LOW |
| CALC-002 | `src/hermes_whisper/config.py`, `src/hermes_whisper/audio.py`, `src/hermes_whisper/model.py`; sample/frame counts | `Nmax=round(sr*T)`; `F=1+floor((max(N,nfft)-nfft)/hop)`; `Fenc=ceil(F/2)` | sr Hz; T s; lengths samples | sample/frame counts | implementation contract | VERIFIED for finite valid inputs | ENG-MEDIUM |
| CALC-003 | `src/hermes_whisper/audio.py`; triangular mel bank | piecewise triangle; area factor `2/(right-left)` | frequency bins Hz, mel bounds | weights dimensionless | comment calls Slaney-style; no citation | REQUIRES AUTHORITATIVE SOURCE | ENG-MEDIUM |
| CALC-004 | `src/hermes_whisper/audio.py`; linear resampling | `M=max(1,round(N*ft/fs))`; linear interpolation | N samples; source/target Hz | M samples | internal | PARTIALLY VERIFIED | ENG-HIGH |
| CALC-005 | `src/hermes_whisper/audio.py`; log-mel features | `power=|FFT|²`; clamp/log10/dynamic-range; `(log+4)/4` | waveform amplitude, bins | normalized features | internal; expected model calibration uncited | PARTIALLY VERIFIED | ENG-MEDIUM |
| CALC-006 | `src/hermes_whisper/tokenizer.py`; timestamp quantization | `i=round(clamp(t,0,Tmax)/Δt)`; count `round(Tmax/Δt)+1` | t, Tmax, Δt seconds | token ID/count | internal | PARTIALLY VERIFIED | ENG-HIGH |
| CALC-007 | `src/hermes_whisper/config.py`; parameter estimate | analytic sum of embedding, encoder, decoder, heads | layer counts/dimensions | parameters count | derived from current modules | VERIFIED algebraically; runtime equality not rerun | ENG-MEDIUM |
| CALC-008 | `src/hermes_whisper/losses.py`; composite objective | `L=Lseq+λctc Lctc+λlang Llang` | dimensionless losses/weights | dimensionless loss | PyTorch primitives; weights have no provenance | PARTIALLY VERIFIED | ENG-HIGH |
| CALC-009 | `src/hermes_whisper/trainer.py`; LR schedule | warmup `max(s,1)/W`; then `rmin+(1-rmin)·0.5(1+cos(πp))` | step counts, ratio | LR multiplier ratio | internal cosine schedule | VERIFIED representative boundaries | ENG-LOW |
| CALC-010 | `src/hermes_whisper/metrics.py`; corpus WER/CER | `ΣLevenshtein(ref,hyp)/Σ|ref|` | word/char sequences | error ratio | standard definition; normalization is project policy | PARTIALLY VERIFIED | ENG-MEDIUM |
| CALC-011 | `src/hermes_whisper/evaluation.py`, `src/hermes_voice_studio/engines/base.py`; RTF | `elapsed seconds / audio seconds` | seconds/seconds | dimensionless ratio | internal | PARTIALLY VERIFIED | ENG-MEDIUM |
| CALC-012 | `src/hermes_whisper/decoding.py`, `src/hermes_voice_studio/engines/faster_whisper.py`; confidence heuristics | Hermes `exp(mean(token log p))`; desktop `exp(avg_logprob)` | decoder/token log probabilities | nominal `[0,1]` score | engine-specific internal heuristics; uncalibrated | NOT VERIFIED as probability | ENG-HIGH |
| CALC-013 | `src/hermes_whisper/decoding.py`; chunking/overlap | `C=sr*Tmax`; `O=round(sr*Toverlap)`; stride `C-O` | Hz, seconds | samples/intervals | internal | PARTIALLY VERIFIED | ENG-HIGH |
| CALC-014 | `src/hermes_whisper/decoding.py`; text overlap merge | maximum exact casefolded suffix/prefix, up to 24 words | chunk word sequences | merged text | internal heuristic | PARTIALLY VERIFIED | ENG-HIGH |
| CALC-015 | `src/hermes_whisper/evaluation.py`; language accuracy | `correct/count` | predicted/reference labels | ratio | internal | FAIL in reference-mode semantics | ENG-HIGH |
| CALC-016 | `src/hermes_whisper/trainer.py`; validation mean | `mean(batch_mean_loss)` | per-batch means | dimensionless mean | internal | PARTIALLY VERIFIED | ENG-MEDIUM |
| CALC-017 | `src/hermes_whisper/manifest.py`; dataset fingerprint | `SHA256(concatenated canonical record JSON)` | record metadata/path strings | digest | internal | NOT VERIFIED as immutable dataset identity | ENG-HIGH |
| CALC-018 | `docs/TRAINING_RUNBOOK.md`, `src/hermes_whisper/trainer.py`; effective batch/exposure | `B=b*accum*world`; `exposures=max_steps*B` | samples/step, steps | sample exposures | internal | VERIFIED algebraically | ENG-MEDIUM |
| CALC-019 | `configs/*.json`, `src/hermes_whisper/checkpoint.py`; storage floor | FP32 weights `4P`; model+Adam `≈12P`; training state floor `≈16P` | parameter count, bytes | bytes/GiB | auditor-derived lower bound | PARTIALLY VERIFIED | ENG-MEDIUM |
| CALC-020 | `src/hermes_whisper/model.py`; attention score memory | per block `heads*T²*element_bytes` | heads, frames, bytes | bytes | auditor-derived upper/materialization estimate | PARTIALLY VERIFIED | ENG-MEDIUM |
| CALC-021 | commercial unit economics | inference `RTF*node cost/h + storage+egress+support`; training `nodes*rate*hours` | measured operational inputs | currency/audio-hour or run | cost identity only | NOT VERIFIED; inputs absent | ENG-HIGH commercial |
| CALC-022 | `src/hermes_voice_studio/exporters.py:9-14`; subtitle timestamp | round seconds to integer milliseconds, then quotient/remainder to `HH:MM:SS,mmm` | seconds | formatted timecode | internal export contract | VERIFIED representative rounding only | ENG-MEDIUM |

## Independent representative checks

| Calculation | Substitution | Independent result | Program result | Deviation / outcome |
|---|---|---:|---:|---|
| Hz→mel | `f=1000 Hz` | `999.9855371396244 mel` | `999.9855371396244` | 0 |
| mel→Hz | preceding mel | `1000.0000000000002 Hz` | same | floating-point roundoff only |
| frames | sr=16k, N=16000, nfft=400, hop=160, center=false | 98 | 98 | 0 |
| resample count | N=10, 10 Hz→20 Hz | 20 samples | 20 | 0 count; spectral validity not established |
| WER | one deletion from two reference words | 0.5 | 0.5 | 0 |
| CER | `český` vs `cesky` under project normalization | 0.4 | 0.4 | 0; policy includes normalization choices |
| subtitle timestamp | 1.9996 s | `00:00:02,000` | same | expected millisecond rounding |
| LR multiplier | W=10,000; S=250,000; min=.1 | step 0=.0001; 9,999=.9999; 10,000=1; 130,000=.55; 250,000=.1 | same | 0 |
| 150m parameter estimate | current config/formula | 150,748,110 | 150,748,110 formula output | actual instantiated Torch comparison not rerun |
| non-finite duration | `duration_seconds=NaN` | must reject | accepted | **FAIL** |

Checks used bundled Python 3.12.13 and direct source imports; they are numerical probes, not full model validation.

## Detailed findings and boundary analysis

### ENG-HIGH — desktop discards merged Hermes text

`transcribe_file()` computes a merged result, but `HermesWhisperEngine` creates overlapping `Segment` objects and desktop `EngineResult.text` joins them. For audio longer than a chunk, duplicate overlap can enter immutable `raw_text` and subtitles. The standalone merge-helper test does not cover the desktop integration.

Required verification: deterministic >30 s fixture with known overlap, assertions for raw text and non-overlapping subtitle contract.

### ENG-HIGH — dataset split and identity are not enforced

Policy docs require speaker/source/document grouping and train-only tokenizer, but runtime validates records individually. It does not enforce train/validation role, unique IDs, cross-manifest group/audio duplicates or tokenizer input role. Closed-test WER/CER can therefore be understated by leakage.

Required verification: strict role-specific manifests, stable unique IDs, audio-content hashes, group/near-duplicate leakage report, immutable snapshot and adversarial tests.

### ENG-HIGH — fingerprint does not bind audio bytes

Current digest includes JSON/path, not file content. Replacing audio in place keeps a fingerprint; relocating identical data changes it. Resume provenance is therefore neither content-complete nor relocation-stable.

### ENG-HIGH — linear resampling is not a complete downsampler

Sample count and interpolation arithmetic are internally consistent. For downsampling, no low-pass/anti-alias filter precedes decimation; high-frequency energy can fold into the passband and change features/labels. The implementation needs an authoritative algorithm/source and frequency-domain golden fixtures.

### ENG-HIGH — CTC infeasibility can be hidden

Collate checks `target_length <= encoder_length`, but repeated adjacent target tokens require additional CTC frames. `zero_infinity=True` can convert infinite losses to zero, removing gradients without an explicit rejected/zeroed counter.

Required condition: `input_length >= target_length + adjacent_repeat_count` plus monitored violation count.

### ENG-HIGH — metric semantics can mislead

- `language_mode=reference` feeds reference language and compares it to itself, constructing 100% accuracy.
- evaluation RTF uses manifest duration rather than decoded/processed duration; desktop timing scope is different and excludes model load separately.
- token confidence is a geometric mean from pre-constraint vocabulary probabilities, not calibrated probability under the decoder.
- CER includes normalized spaces; WER/CER normalization policy is valid only if explicitly frozen and reported.
- validation loss weights each batch mean equally, so an incomplete batch can be over-weighted.

Metrics must name scope/policy and avoid probability/accuracy labels that the calculation cannot support.

### ENG-HIGH — validation accepts non-finite inputs

`NaN <= 0` and `abs(actual - NaN) > tolerance` are false, so manifest/data checks can accept NaN duration. Config/frequency/timestamp bounds similarly need explicit finite checks and resource maxima. Non-finite metadata can poison duration totals, RTF and reports.

### ENG-MEDIUM — numerical/resource lower bounds

For 16 kHz, 30 s, nfft=400/hop=160: 480,000 max samples, 2,998 mel frames and 1,499 encoder frames.

Current parameter estimates:

- nano: 26,874,830 parameters; FP32 weights ≈102.5 MiB; simple training-state floor ≈0.40 GiB;
- 150m: 150,748,110 parameters; FP32 weights ≈575.1 MiB; simple training-state floor ≈2.25 GiB.

These exclude activations, allocator workspace, DDP duplication and serialization overhead. At 1,499 encoder frames and 8 heads, a single attention score tensor can have about 17.98 million elements per sample/block (roughly 34.3 MiB BF16 or 68.6 MiB FP32 if materialized). Actual fused kernels differ; measured RAM/VRAM is required.

Checkpoint lower-bound accumulation without pruning can reach tens to hundreds of GiB across current max-step/interval settings. It is a capacity-planning warning, not an observed run.

## Source/standards status

No professional standard compliance claim was found. Mel/filterbank normalization, feature calibration, confidence interpretation and model-quality methodology lack sufficient authoritative in-repo references. They remain `REQUIRES AUTHORITATIVE SOURCE` or `PARTIALLY VERIFIED`; do not infer conformance from common-looking formulas.

The official product-quality gate must additionally define:

- immutable licensed data and closed test set;
- normalization/tokenizer/model version;
- WER/CER slices and statistical uncertainty;
- target hardware, cold/warm RTF, RAM/VRAM and energy/cost scope;
- calibration/error-detection behavior;
- reproducible training/resume/build artifacts.

## Engineering release rule

No Hermes accuracy, probability, real-time or cost claim is authorized until the relevant calculation is `VERIFIED`, integration semantics are regression-tested, data leakage/provenance gates pass, and measured evidence is attached to an immutable model release.
