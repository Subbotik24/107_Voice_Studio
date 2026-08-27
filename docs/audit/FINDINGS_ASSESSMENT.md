# Findings register assessment — independent re-verification

Assessment date: 2026-08-27. Assessed tree: `claude/analyze-error-list-ddvd5j`
at `65d885e0ef3e3c0571c9933dfafb89d8d3a45094` (identical to `main` at the time
of assessment).

This document re-checks every finding in `docs/audit/FINDINGS_REGISTER.md`
against the code as it stands today. It does not replace the register: the
register records what was found at its own baselines, and those records stay
valid for those baselines. This document records which findings survive
re-verification, which have been overtaken by code that landed afterwards, and
where the register's own severity or evidence no longer matches the tree.

The register's citations are anchored at `main@fffa50b6` (original audit) and
`672577ef` (W1). Sixteen commits have landed since. Where a citation no longer
resolves to the described code, that is noted as register hygiene, not as a
change in the underlying defect.

## Method and evidence boundary

Four independent read-only reviews were run over disjoint finding clusters
(desktop correctness/reliability, security/privacy, Hermes/ML, release/QA/
commercial), each required to cite current `file:line` evidence and to name the
regression tests that do or do not back each claim. Behavioural probes were run
where they were possible without a real device, network, or GPU.

Local verification environment: Linux, CPython 3.12.3, project installed with
`[dev,cloud,benchmark]`. This is **not** a supported product platform. The
product targets Windows 10/11 x64 and macOS Apple Silicon; every native,
device, clipboard, and OS-ACL claim below remains **NOT RUN**, exactly as the
register states. `torch` is absent, so the two Hermes model tests are skipped
here.

| Command | Status | Result |
|---|---|---|
| `python -m compileall -q src tests` | PASS | exit 0 |
| `python -m pytest -q` | PASS | `273 passed, 2 skipped, 5 subtests passed in 4.25s` |
| `python -m ruff check src tests scripts` | PASS | `All checks passed!` |
| `python -m build --wheel` | PASS | `hermes_voice_studio-0.3.0rc1-py3-none-any.whl` |
| `python -m pip check` | PASS | `No broken requirements found.` |
| `python scripts/check_workflow_pins.py .github/workflows` | PASS | `Workflow policy passed for 1 path(s).` |

The `pytest` line above is the state **after** the fix described in QA-003. On
the assessed commit, before that fix, the same command did not terminate.

## QA-003 — the suite hangs forever on POSIX, and CI on `main` is down

This is a new finding. It is not in the register, and it is the most urgent
item in this document.

- **Severity:** P1. Impact `C0 S0 Cm2 P3 E2 M2`. Test-only defect, no product
  behaviour affected, but it removes the evidence base every other finding is
  verified against.
- **Status:** fixed in this change; see *Change applied* below.

`tests/test_jobs_app.py::test_prepare_consumed_deadline_is_not_reset_for_inference`
substituted a frozen virtual clock by patching `operation.time.monotonic`.
`operation.time` is not a module-local alias — it is the stdlib `time` module —
so the patch froze `time.monotonic` for the whole process.

POSIX `multiprocessing.connection.wait()` recomputes the remaining timeout of a
timed wait from `time.monotonic()` on every loop iteration:

```
timeout = deadline - time.monotonic()
if timeout < 0:
    return ready
```

Against a frozen clock that remainder is constant, never goes negative, and the
loop never exits. The transcription worker poll at `src/hermes_voice_studio/jobs.py:148`
(`self._results.get(timeout=wait_seconds)`) therefore spins at 10 Hz forever
instead of raising `queue.Empty`.

Windows is unaffected: its `wait()` converts the timeout to milliseconds once
and hands it to `WaitForMultipleObjects`, with no monotonic loop.

Confirmed three ways:

1. **Local stack dump.** `pytest -o faulthandler_timeout=25` on that single test
   parks in `selectors.select` ← `connection.wait` ← `connection._poll` ←
   `queues.get` ← `jobs.py:148`.
2. **Minimal reproduction**, independent of this repository: setting
   `time.monotonic = lambda: 1.1` and then calling `q.get(timeout=0.1)` on a
   `multiprocessing` queue prints `STILL BLOCKED after 5s`.
3. **Production CI.** The two most recent CI runs on `main` both hit GitHub's
   six-hour job ceiling and were cancelled, on the `python -m pytest -q` step:

| Run | Commit | macOS 3.11 / 3.12 | Windows 3.11 / 3.12 |
|---|---|---|---|
| [32361319763](https://github.com/Subbotik24/107_Voice_Studio/actions/runs/32361319763) | `7a72465` (before the fix wave) | success, ~85 s | success |
| [32738101530](https://github.com/Subbotik24/107_Voice_Studio/actions/runs/32738101530) | `2969f60` | cancelled after ~6 h | success |
| [32847177856](https://github.com/Subbotik24/107_Voice_Studio/actions/runs/32847177856) | `65d885e` (assessed HEAD) | cancelled after ~6 h | success, 14 s |

In run `32847177856` the `pytest` step ran from `12:22:14Z` to `18:22:28Z` on
both macOS jobs; `build`, `pip check`, and `pip_audit` were then skipped. The
run reports as *cancelled* rather than *failed*, which is why it reads as
ambiguous rather than broken at a glance.

Two consequences worth stating plainly. Every push to `main` since 2026-08-24
has burned roughly twelve hours of macOS runner time and produced no wheel,
`pip check`, or `pip-audit` result. And the `189 passed` / `82 passed` evidence
recorded in `docs/audit/AUDIT_LEDGER.md` for the final fix wave cannot be
reproduced on Linux or macOS — it is reproducible only on Windows, which is
consistent with that evidence having been captured on a Windows host.

`tests/test_operation_app.py` and `tests/test_storage_app.py` patched the same
global clock. They did not hang, because neither spawns a process — but they
carried the same latent trap.

### Change applied

`OperationBudget` now reads time through a module-level seam,
`operation._monotonic()`, instead of calling `time.monotonic()` directly, and
the five test sites substitute that seam instead of the stdlib clock. The
budget's semantics are unchanged. `tests/test_operation_app.py::test_budget_clock_is_substitutable_without_replacing_the_stdlib_clock`
pins the invariant and records why it exists.

Before: the suite did not terminate (killed at 600 s locally; 6 h in CI).
After: `273 passed, 2 skipped, 5 subtests passed in 4.25s`.

## Verdict summary

Vocabulary: `CONFIRMED_OPEN` — still true as described. `PARTIALLY_RESOLVED` —
some sub-claims closed, the remainder named. `RESOLVED_IN_CODE` — the described
defect is gone from the code; any un-run native acceptance is noted separately.
`AS STATED` — the register's own split status is accurate.

Of 34 active findings: 24 `CONFIRMED_OPEN`, 4 `PARTIALLY_RESOLVED`,
4 `RESOLVED_IN_CODE`, 1 `RESOLVED_DOCS`, 1 `AS STATED`. No finding was found to
be fabricated, and only one — `PERF-001` — understates work that already
exists. Every ENG finding is open and none has a regression test.

| ID | Register | Verdict | Recommended severity |
|---|---|---|---|
| COR-002 | P1 | RESOLVED_IN_CODE | P3 — decode layer closed and tested |
| COR-003 | P2 | CONFIRMED_OPEN | P2 — unchanged |
| COR-004 | P1 | RESOLVED_IN_CODE | P2 — pending native |
| ENG-001 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| ENG-002 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| ENG-003 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| ENG-004 | P1 | CONFIRMED_OPEN | P2 — research CLI only |
| ENG-005 | P1 | CONFIRMED_OPEN | P1 — reaches the desktop Hermes path |
| ENG-006 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| ENG-007 | P2 | CONFIRMED_OPEN | P2 — unchanged |
| ENG-008 | P2 | CONFIRMED_OPEN | P2 — unchanged |
| ARC-001 | P2 | CONFIRMED_OPEN | P2, leaning P1 — regressed since baseline |
| REL-001 | P1 | RESOLVED_IN_CODE | P2 — native acceptance only |
| REL-002 | P1 | PARTIALLY_RESOLVED | P2 — residue belongs to SEC-001 |
| REL-003 | P1 | PARTIALLY_RESOLVED | **P1 — strongest open desktop finding** |
| REL-004 | P2 | CONFIRMED_OPEN | P2 — promote path under-stated |
| REL-005 | P2 | CONFIRMED_OPEN | P3 as written; real hazard misidentified |
| REL-006 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| SEC-001 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| SEC-002 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| SEC-003 | P1 | CONFIRMED_OPEN | P1 — primitives landed, unwired |
| PRV-001 | P2 / P1 temp | PARTIALLY_RESOLVED | temp closed; diagnostics open |
| PRV-002 | P1 | CONFIRMED_OPEN | P1 — under-stated on POSIX |
| PRV-003 | P1 | RESOLVED_IN_CODE | P3 — native acceptance only |
| QA-001 | P1 | CONFIRMED_OPEN | P1 — under-stated |
| QA-002 | P2 | CONFIRMED_OPEN | P2 — unchanged |
| QA-003 | *new* | FIXED IN THIS CHANGE | P1 |
| SUP-001 | P1 | CONFIRMED_OPEN | P1 — unchanged |
| CI-001 | P2 | AS STATED | P2 — implemented half verified |
| IP-001 | P1 | CONFIRMED_OPEN | P1 — under-stated |
| COM-001 | P1 | CONFIRMED_OPEN | readiness gate, not a defect |
| OPS-001 | P2 | CONFIRMED_OPEN | P2 — under-stated |
| DOC-001 | P2 | RESOLVED_DOCS | closed |
| MNT-001 | P3/P2 | CONFIRMED_OPEN | P2 — README actively misdirects |
| PERF-001 | P2 | PARTIALLY_RESOLVED | P2 — harness exists |

## Desktop correctness and reliability

**COR-002 — RESOLVED_IN_CODE.** `models.py:123-131` rejects every wrong type
across all 13 string and 3 boolean fields, using `type(x) is not bool` so
`1`-for-bool and `True`-for-int are both caught; `config.py:44-50` normalises
`OSError`/`JSONDecodeError`/non-object into `ValueError`; `app.py:44-49`
recovers to defaults with a warning. `tests/test_config_app.py:48` covers the
matrix. One residue the register does not name: `app.py:504` reloads settings
after a backup restore with no `try`/`except`, so a `ValueError` there raises
inside the Tk callback. The app-level recovery path itself has no test.

**COR-003 — CONFIRMED_OPEN.** `diagnostics.py:102-105` builds its dependency
list for `faster-whisper` and `hermes-whisper` only; there is no `openai-cloud`
branch, the `openai` package is never required, and no key status is consulted.
The `else` branch at `:127-136` then looks a *cloud* model id up in the *local*
catalogue. Both failure directions are real: a valid cloud setup reports
`incomplete`, and a cloud setup with no package and no key reports `ok` if a
local `small` happens to be installed. No test passes `engine="openai-cloud"`
to `diagnostics()`. Citation is stale (`:99-139` → `:87-139`).

**COR-004 — RESOLVED_IN_CODE.** `_editor_is_dirty` (`app.py:962-968`) and
`_confirm_editor_transition` (`:970-981`) are wired at all six transitions —
`app.py:540, 798, 908, 931, 1055, 1566` — and a failed Save aborts the
transition rather than discarding. `_save_edits` writes only `corrected_text`
and formatting through a single transaction (`storage.py:451-479`), never
touching `raw_text`. `tests/test_editor_state_app.py` covers save/discard/
cancel, save-error, close-cancel, and the draft case behaviourally. Live Tk
interaction remains NOT RUN.

**REL-001 — RESOLVED_IN_CODE.** Every constant the register names exists
(`recorder.py:15-19`), the queue is bounded with `put_nowait` and a drop counter
(`:166-169`), the two-hour cap is enforced against accepted frames (`:148-161`),
audio streams to a private WAV on a writer thread (`:287-321`), and degraded
capture defaults to rejection (`app.py:661-679`, `default=messagebox.NO`). All
named tests exist and pass. Real microphone overflow, device disconnect, disk
pressure, and two-hour wall-clock remain NOT RUN and are not determinable
without hardware.

**REL-002 — PARTIALLY_RESOLVED.** The expensive half is fixed: the budget is
constructed before `service.prepare` (`jobs.py:114-126`), and the copy loop
checkpoints three times per 1 MiB block (`storage.py:132-156`), with genuine
mid-copy cancel and timeout tests at `tests/test_storage_app.py:353-434`. What
remains open is the PyAV probe: `service.py:66` calls `validate_media_file` as
one uninterruptible call, and `jobs.py:126` never passes the `max_bytes`
ceiling that `service.prepare` accepts. That residue is the same surface as
SEC-001 and is better tracked there than as a separate P1.

**REL-003 — PARTIALLY_RESOLVED, and the strongest open desktop finding.** The
microphone-temp slice is genuinely closed and tested. The restore window is
not. `app.py:1499-1503` starts restore on a daemon thread; `_close`
(`app.py:1565-1580`) has no busy gate and no join before `self.destroy()`. The
vulnerable window is `backup.py:212-223`: between `data_root.replace(recovery)`
and `temporary.replace(data_root)` **no active data root exists**, and the
`except BaseException` rollback does not run when a daemon thread is killed at
interpreter exit. Additionally — not in the register — there is no startup
reconciliation: `_cleanup_temp` (`app.py:320-350`) only removes paths already
in the in-session `_pending_microphone_files` set, so temp WAVs from a prior
crash persist indefinitely. No test starts a restore and then closes the app.

**REL-004 — CONFIRMED_OPEN.** Retention unlinks the file at
`storage.py:578` and only then clears `source_path`/`audio_retained` and saves
at `:585`. Catalogue promote does `temporary.replace(target)` at
`model_catalog.py:162` and persists at `:175`. The promote case is sharper than
the register states: a crash between those two lines leaves an *untracked*
directory that then permanently blocks reinstall via the `FileExistsError` at
`:160-161`, with no recovery short of a manual delete. No failure-injection
test exists for any of these paths.

**REL-005 — CONFIRMED_OPEN literally, but the stated mechanism is the wrong
one.** There is indeed no `close()`, `join_thread()`, or `cancel_join_thread()`
anywhere (`jobs.py:86-96` drops the queues by assignment). But CPython installs
`Finalize` handlers on queue objects, so dropping the last reference normally
does reclaim the feeder thread and pipe descriptors by refcount — which makes
the register's "Windows handle accumulation over a long session" the weaker
reading. The real hazard is narrower: after `process.terminate()`, a feeder
thread blocked writing into a full pipe with no reader can make that finalizer's
`join()` block, and it would block on the **Tk main thread** during GC, since
`close()` and `restart()` are called from `app.py:1576` and `:1547`.
`cancel_join_thread()` after terminate is the standard mitigation and is absent.

**ARC-001 — CONFIRMED_OPEN, and regressed.** `app.py` is now 1,588 lines, not
the ~1,219 the register records — about 30% growth since baseline. Five heavy
operations still run inline on the Tk thread: `catalog.import_local`
(`app.py:1390-1394`, a full `copytree` plus SHA-256 of every file — roughly
520 MB for `small`, 3.2 GB for `large-v3`), `catalog.verify` (`:1438-1442`),
`catalog.remove` (`:1454-1458`), a live `OpenAI().models.list()` (`:1262-1269`,
bounded at 30 s), and `verify_bundle` (`:1291-1305`). Download, backup,
transcription, and AI cleanup *are* correctly threaded, so the pattern is
inconsistent rather than absent — and none of the five inline paths calls
`_set_busy`, so there is not even a busy indicator. A multi-minute frozen
window with an OS "not responding" overlay and no cancel is the most likely
support ticket in this set.

## Security and privacy

**SEC-001 — CONFIRMED_OPEN.** `media.py` is byte-identical to the audit
baseline. PyAV decodes in the calling GUI/CLI process (`service.py:66` runs
before `jobs.py:129` starts the worker), and the ffmpeg child at
`media.py:73-95` has no `timeout=`, no process group, no rlimits, and buffers
stderr unbounded via `capture_output=True`. A repo-wide grep for `setrlimit`,
`RLIMIT`, `start_new_session`, `preexec_fn`, or `CREATE_NEW_PROCESS_GROUP`
returns nothing in `src/`. A second unisolated parse surface not in the
register: `engines/openai_cloud.py:29-31` opens `av` and swallows every
exception. No malformed-media fixtures exist. No memory-corruption or RCE claim
is made here; what the code demonstrably permits is unbounded parse time and an
ffmpeg child with no deadline — a hang of the GUI on a crafted or slow file.

**SEC-002 — CONFIRMED_OPEN.** A repo-wide grep for `signature`, `sigstore`,
`gpg`, `minisign`, `public_key`, or `publisher` across `src/` and `scripts/`
matches only ZIP magic-byte constants. `model_release.py:24-34` does not require
HTTPS for the *registry* itself (only the asset URL is scheme-checked at
`:44-45`), and the registry origin is whatever `HVS_MODEL_REGISTRY_URL` points
at — so the SHA-256 at `:46-51` binds the archive to a manifest the same
attacker would control. `model_catalog.py:218` defaults `revision=None`, an
unpinned mutable Hub ref. One mitigation the register does not credit:
`bundle.py:301` loads with `torch.load(..., weights_only=True)`, which blocks
the classic pickle-RCE path.

**SEC-003 — CONFIRMED_OPEN, with a nuance that matters more than the finding
text.** The five archive-safety commits landed real work — `src/hermes_common/archive.py`
is 820 lines of budgets, pre-`zipfile` EOCD/ZIP64 preflight, a canonical-path
trie, symlink rejection, and per-member and total compression-ratio bounds,
with 55 passing tests. It has **zero production callers**: the only importer is
its own test file, and `docs/CONTINUATION_PLAN_0.4.md:101` confirms wiring it
in is future work. `backup.py` and `hermes_whisper/bundle.py` are byte-identical
to the audit baseline. Demonstrated non-destructively: a 101 KB backup declaring
a 100 MB `transcripts.jsonl` (ratio 1013:1) still returns `verify_backup → PASS`,
and the `archive.read` at `backup.py:163` peaked at 219.7 MB traced allocation.
One register sub-claim is wrong: member count *is* bounded for `.hws`
(`bundle.py:125` requires an exact 5-member set), though `model.pt` itself has
no ceiling and is streamed to disk before its hash is checked (`:237-244`). The
sub-claim remains correct for backups. The register entry should gain a
sub-state — *primitives implemented and tested; integration not started* —
rather than being marked resolved on the strength of the commit titles.

**PRV-001 — PARTIALLY_RESOLVED**, exactly as the register scopes it. The
microphone-temp half is implemented (`app.py:563-583`, `recorder.py:404-406`,
`0o700`/`0o600`, symlink rejection, tracked ownership) and genuinely tested.
The diagnostics half is untouched: `_redact_paths` (`diagnostics.py:17-25`)
drops any key literally named `paths` and does one case-sensitive
`str(Path.home())` substitution. Anything outside `$HOME` still ships verbatim
in an exported report — a user-chosen `.hws` path at `:111`, a custom model
directory at `:146`, the ffmpeg path at `:150`, and raw `str(exc)` text at
`:148` and `:158-165`. There are zero tests for redaction.

**PRV-002 — CONFIRMED_OPEN, and under-stated.** The register says
confidentiality "depends on OS/profile defaults", which reads as neutral.
Measured on POSIX with umask 022: data root `0o755`, `sources/` `0o755`,
`history.sqlite3` `0o644`, managed audio copies `0o644`, `settings.json`
`0o644`. The transcript database and imported audio are world-readable by
default inside world-traversable directories. `storage.py:55-58` calls `mkdir`
with no `mode=` and no post-`chmod`; the only permission enforcement anywhere
in the product is the microphone temp path. Windows ACL inheritance genuinely
requires a real Windows run and stays NOT RUN.

**PRV-003 — RESOLVED_IN_CODE.** `auto_copy` defaults to `False`
(`models.py:115`), the only unconditional clipboard write is the user-pressed
toolbar button (`app.py:792-793`, `:863-868`), the settings label discloses OS
clipboard history and sync (`app.py:1218-1224`), and
`tests/test_config_app.py:63` pins the default. What remains is inherent to any
Copy button plus unverified OS clipboard-manager behaviour; that belongs on the
native acceptance checklist, not on the P1 release-blocker list.

## Hermes / ML research track

`src/hermes_whisper/` and `src/hermes_voice_studio/engines/` are byte-identical
to both the original audit baseline and the W1 baseline. No ENG finding can
have been fixed, and every line citation into those two trees is exact. **No
regression test exists for any of ENG-001 through ENG-008**; the three nearest
tests each assert something weaker than the finding claims.

**ENG-001 — CONFIRMED_OPEN**, reproduced. `decoding.py:181-183` computes the
merged text and returns it as `Transcription.text`, but
`engines/hermes.py:56-65` builds segments from `result.chunks` only and never
reads `result.text`; `engines/base.py:20-23` re-joins the raw overlapping chunk
texts. Probe output, using the real merge helper and adapter:

```
decoding merged text : це технічний опис проєкту для замовника
adapter EngineResult : це технічний опис проєкту опис проєкту для замовника
segment ranges: [(0.0, 30.0), (29.0, 59.0)]  overlap seconds: 1.0
```

The duplicated text is persisted as immutable `raw_text` (`service.py:83,105` —
the register's `service.py:44-45` is a stale citation, behaviour unchanged), and
the overlapping ranges are written verbatim into SRT/VTT cues
(`exporters.py:44-45`).

**ENG-002 — CONFIRMED_OPEN.** `manifest.py:39-70` is entirely record-local:
`split` is checked against a value set but never bound to the manifest it lives
in, `record_id` is optional and never checked for uniqueness or cross-split
collision, and no speaker/source/document/audio disjointness check exists
anywhere. `cli.py:54-72` lets a test-split manifest train the tokenizer;
`:119-139` accepts identical audio paths in train and validation silently;
`:185-207` accepts any manifest for evaluation.

**ENG-003 — CONFIRMED_OPEN.** `manifest.py:158-168` hashes
`record.to_json_dict()` with no base directory, so the payload carries the
absolute path resolved at parse time and no audio bytes are read. Replacing a
file in place keeps the digest; relocating an identical corpus changes it.
`trainer.py:259-260` uses that digest as the sole resume gate and
`checkpoint.py:110` stores it as the provenance claim. The one existing test
asserts only self-equality.

**ENG-004 — CONFIRMED_OPEN**, reproduced end to end. The root cause is
single-sided comparison: `nan <= 0` is `False`, so `manifest.py:53-54` accepts
it. Through the real CLI, with JSON's bare `NaN`:

```
$ python -m hermes_whisper validate-manifest nan.jsonl --skip-audio-check
{ "status": "PASS", "records": 1, "duration_hours": NaN, ... }   exit=0
```

The same class of gap exists at `config.py:31-32` (`max_audio_seconds`) and
`:141-142` (`learning_rate`). Chained comparisons elsewhere — `dropout` at
`config.py:75-76`, segment bounds at `manifest.py:60` — correctly reject NaN,
so the exposure is specifically the single-sided guards. `tokenizer.py:150-151`
and `audio.py:211` already use `math.isfinite` and show the intended pattern.

**ENG-005 — CONFIRMED_OPEN**, measured, and it reaches the desktop more
directly than the register states. `audio.py:104-115` uses `np.interp` with no
low-pass stage. Measured 1-second tones, 48 kHz → 16 kHz:

```
input 15000 Hz -> peak  999.0 Hz   (rms 0.5192)
input 20000 Hz -> peak 4001.0 Hz   (rms 0.4347)
input  7000 Hz -> peak 7000.0 Hz   (rms 0.6594, in-band reference)
```

15 kHz folds to ~1 kHz and 20 kHz to ~4 kHz, at 60–79% of the in-band tone's
RMS — inside the speech band, not a marginal artefact. `engines/hermes.py:46`
wraps decoding in `canonical_wav`, but `media.py:63-65` passes any `.wav`
through untouched, so a 44.1/48 kHz WAV aliases at inference time on the
shipping desktop path whenever the Hermes engine is selected. The one existing
test asserts output length, not frequency response.

**ENG-006 — CONFIRMED_OPEN**, arithmetic checked. `data.py:161-168` rejects
only `ctc_length > encoder_length`; the correct condition is
`input >= target + adjacent_repeats`, because each repeated token needs an
intervening blank. `losses.py:55-63` passes `zero_infinity=True`, so infeasible
samples yield loss 0 and zero gradient with no counter and no warning. Probe
against the real tokenizer: the text `aabbcc` has 3 adjacent repeats and needs
9 frames, and is accepted at an encoder length of 6. Byte-level BPE makes
adjacent repeats common in ordinary text.

**ENG-007 — CONFIRMED_OPEN**, all four sub-claims. Reference-mode language
accuracy is tautologically 1.0: `evaluation.py:42` passes the reference label
in, `decoding.py:118` adopts it, and `evaluation.py:47` then always counts a
match — yet it is reported as a metric at `:65` and `--language-mode reference`
is a first-class CLI choice. `real_time_factor` names two different
measurements with different numerators and denominators
(`evaluation.py:58-64` versus `engines/base.py:26-29`). `decoding.py:94-96` is
an uncalibrated mean token probability under greedy argmax, surfaced as
`Segment.confidence` and stored in desktop transcripts. `trainer.py:386`
averages per-batch means over batch count while validation keeps a short final
batch (`:217`). `metrics.py` itself aggregates correctly — the defect is the
surrounding labels and scopes, as the register says.

**ENG-008 — CONFIRMED_OPEN**, all three sub-claims. `data.py:131-163`
zero-pads to the batch maximum and `model.py:55-76` applies `BatchNorm1d` across
the full padded time axis, with masking only outside the conv module;
`trainer.py:143-147` wraps in DDP with no `SyncBatchNorm` conversion anywhere,
so those statistics are rank-local. `checkpoint.py:94-100` persists step,
optimizer, scheduler, and scaler but no RNG state, epoch, or sampler position,
and `trainer.py:277-301` restarts the loader at epoch 0 — a resumed run does
not reproduce a continuous one. `tokenizer.py:149-154` clamps an out-of-range
timestamp to a valid id with no error, and nothing ties
`max_timestamp_seconds` to `max_audio_seconds`.

Scope note: only ENG-001 and ENG-005 touch the shipping desktop binary, and
only through the opt-in `hermes-whisper` engine with a user-supplied `.hws`
bundle. ENG-002/003/004/006/008 are research-track only; ENG-007 is
research-track except for the uncalibrated per-segment confidence.

## Release, QA, supply chain, commercial

**QA-001 — CONFIRMED_OPEN, and under-stated.** `ci.yml:28` installs
`.[dev,cloud]`; the `hermes` and `train` extras are never installed by any
workflow. There is exactly one torch skip marker in the suite
(`tests/test_model.py:18`) guarding the only forward/loss/backward coverage that
exists — so those tests are not merely *able* to be skipped, they are
*unconditionally* skipped on every CI run, and `-q` does not fail on skips.
`hermes_whisper` has 18 modules and test files for 7; there is no
`test_trainer.py`, `test_losses.py`, `test_data.py`, `test_decoding.py`,
`test_evaluation.py`, or `test_checkpoint.py` — the modules cited by
ENG-001/006/007/008 have no direct tests at all. `smoke-test` exists
(`hermes_whisper/cli.py:314`) and is invoked by no gate.

**QA-002 — CONFIRMED_OPEN**, with a sharper framing than the register's. The
source-assertion problem is one of concentration, not volume: 14 of 206
collected tests assert on file or source text, but that is 100% of GUI-contract
coverage (`test_gui_contract_app.py`, all 5 tests, 10 `inspect.getsource` calls,
including one that asserts on `str.index` ordering of two substrings) and 100%
of packaging and launcher coverage. `test_platform_acceptance_app.py:12-35`
proves the acceptance *harness* reports PASS against a canned fixture and
proves nothing about transcription. No coverage or type gate exists —
`grep mypy\|coverage\|pytest-cov\|--cov` over `pyproject.toml`, workflows, and
scripts returns nothing, and no `setup.cfg`, `mypy.ini`, `.coveragerc`, or
`tox.ini` is present, despite the codebase being heavily annotated. One drift
not in the register: `scripts/quality_gate.sh:27-28` lints
`src tests scripts packaging` while `ci.yml:30-31` lints only `src tests`, so
`scripts/` and `packaging/` are unlinted in CI and the local gate is strictly
stronger than the enforced one.

**REL-006 — CONFIRMED_OPEN.** The entire provenance binding in
`create_release_manifest.py:84-113` is one line: `"repository_metadata":
"present" if (repository_root / ".git").exists()`. No commit SHA, no tree hash,
no dirty-tree check, no branch, no tag, no dependency list, no SBOM. `:86`
hardcodes the version as a string literal, and `build_test_rc.sh:17,84,110`
hardcodes it twice more — three independent copies. `:12-15` checks only that
the build tools are importable, never which versions. `SHA256SUMS.txt` is
unsigned. The repository's own `docs/DEVELOPMENT_DESCRIPTION.md:136` states the
same conclusion.

**SUP-001 — CONFIRMED_OPEN.** `git ls-files | grep -iE "lock|constraint|
requirement|sbom|cyclonedx|spdx"` returns nothing. Every dependency is a range,
two of which change native binary payloads (`av>=14,<19` spans five majors and
bundles FFmpeg; `numpy>=1.26,<3` spans the 1.x→2.x ABI break;
`openai>=1.0,<3` spans two majors of a network API). `THIRD_PARTY_NOTICES.md`
is 13 lines and self-describes as an index, naming ~10 packages with no
versions and no license identifiers. `pip-audit` does run in CI, but against
whatever the resolver picks that day.

**CI-001 — AS STATED.** The implemented half verifies by execution:
`python scripts/check_workflow_pins.py .github/workflows` returns
`Workflow policy passed for 1 path(s).` with exit 0, all three workflows use
full 40-character SHAs with version comments, `persist-credentials: false` is
on every checkout, permissions are narrow, and the policy check runs as a CI
step so it is self-enforcing. Dependabot covers both `pip` and `github-actions`.
The open half is genuinely unverifiable in-repo, with one exception: no
`CODEOWNERS` file exists, and that is the one branch-protection-adjacent control
that would be repository-visible.

**IP-001 — CONFIRMED_OPEN, and under-stated.** `Provenance`
(`manifest.py:13-24`) validates `source` and `license` for non-emptiness only,
so `license="whatever"` passes, and `consent` is a bare bool with no grantor,
date, scope, or revocation reference. The `.hws` sub-claim is stronger than
written: `bundle.py:125` enforces **exact set equality** against a 5-member
list, so a bundle is not merely permitted to omit a license or model card — it
is structurally forbidden from carrying one. Shipping rights evidence inside the
artifact needs a format change, not a policy change.

**COM-001 — CONFIRMED_OPEN as fact, disputed as a P1 finding.** The absence is
total and verified: `grep -rlniE "entitlement|license_key|subscription|billing|
activation|tenant|account_id" src/` returns nothing, and there is no updater or
rollback. But nothing here is *broken* — a privacy-first local desktop app
correctly has no account system, and the register's own verification line
concedes the blocker is "business approval". A finding whose blocker is an
unmade business decision is a readiness gate, not an audit finding; carrying it
as P1 alongside genuine correctness bugs inflates the P1 count. The `Cm3`
impact code already carries the commercial weight.

**OPS-001 — CONFIRMED_OPEN, and under-stated.** `grep -rn "getLogger" src/`
returns **zero** matches: there is no logging framework of any kind at any
level, not merely no *structured* logging. No audit event schema, no updater, no
rollback. And `SECURITY.md:53-56` currently asks reporters to send findings to
a contact "once one is configured" and, until then, not to publish — i.e. the
policy asks people to sit on vulnerabilities indefinitely, and no contact
exists. That sub-item is a five-minute fix and is arguably the highest
value-per-effort item in the whole register; burying it inside a P2 omnibus is
plausibly why it has not been done.

**DOC-001 — RESOLVED_DOCS confirmed.** `SECURITY.md:6-7` now affirmatively
states the cloud adapters are present and consent-gated, a stale-phrase probe
finds nothing, and the claim matches the code: `--allow-cloud-upload` and
`--allow-cloud-text` are enforced at `cli.py:423-425` and `:345-347`, and
`models.py:157-158` hard-blocks the cloud engine under `offline_only` at
validation time. One new defect: `SECURITY.md` contains two concatenated
documents with two top-level headings (`# Security and privacy` at line 1,
`# Security policy` at line 49) and two overlapping defaults sections. Both
halves agree, so it is not a correctness problem — but it is the same class of
drift DOC-001 was raised about.

**MNT-001 — CONFIRMED_OPEN**, all three fields provably dead. `output_dir` is
read only by `normalized_output_dir`; `normalized_output_dir` has **zero
callers** anywhere in `src/`, `tests/`, or `scripts/`; `insert_to_active_app`
has no behavioural reader and `SECURITY.md:42` separately documents the feature
as disabled. All three are still serialised into every user's settings file and
type-validated on load, so a user editing settings sees `output_dir` and
reasonably infers exports go there. They do not. The build drift is now
internally contradictory: `WINDOWS_BUILD_README.md:14` documents
`dist\0.2.0-test-rc2-windows-x64\` while `build_windows_exe.bat:36` and
`build_windows.ps1:2` emit `0.3.0-test-rc1-windows-x64` — the README points a
user at a directory the build no longer creates. `scripts/build_mac.sh:21` is
two minors stale and duplicates `build_test_rc.sh`. `test_packaging_app.py`
asserts on the build scripts but not on the README, which is why this drifted
undetected.

**PERF-001 — PARTIALLY_RESOLVED.** The register's evidence line understates the
tree: `src/hermes_voice_studio/benchmark.py` already implements a measurement
harness emitting `aggregate_rtf`, `peak_rss_bytes`, per-record
`real_time_factor` and `model_load_seconds` (so cold-versus-warm is
measurable), and it refuses unlicensed benchmark corpora. What is genuinely
missing: no committed measurement artifact exists (`git ls-files` finds the code
and its test, never an output), no VRAM sampling, no package/storage/API/support
cost inputs, and the only test of the harness uses a fake engine returning
canned identical text. The risk the finding guards against — shipping an
unfounded performance claim — is currently mitigated by the code making no
claim at all (`"accuracy_claim": "none"`).

## Register hygiene

These do not change any verdict, but they cost a reader time.

- Stale line citations, where the finding is still true but the cited lines no
  longer resolve to the described code: COR-003, REL-002, REL-003, REL-004,
  ARC-001, PRV-002, and ENG-001's `service.py:44-45`.
- ARC-001 records `app.py` at ~1,219 lines; it is 1,588.
- SEC-003's "member count not bounded" is false for `.hws` and true for backups.
- Claims asserted with no backing test: COR-002's app-recovery path, every
  REL-004 claim, PRV-001's redaction, and all eight ENG findings.
- `AUDIT_LEDGER.md` records `189 passed` / `82 passed` for the final fix wave.
  That is not reproducible on Linux or macOS at the assessed commit — see
  QA-003 — and should be annotated with the platform it was captured on.

## Recommended order of work

1. **QA-003** — done in this change. Nothing else can be verified until the
   suite terminates on POSIX.
2. **REL-003 restore window** — the only open finding that can destroy a user's
   active data root. Gate close on an in-progress restore, join the thread, and
   add startup reconciliation for orphaned temp files.
3. **OPS-001 security contact** — one line of `SECURITY.md`; the current text
   asks reporters to withhold vulnerabilities.
4. **QA-001 skip-means-fail** — a Hermes CI lane with pinned Torch, so the
   ENG findings become falsifiable. Everything in the ENG cluster is currently
   unverified by any test.
5. **SEC-003 integration** — wire `hermes_common.archive` into `backup.py` and
   `bundle.py`. The primitives and their tests already exist; only the wiring
   is missing.
6. **ENG-001 and ENG-005** — the two research-track defects that reach the
   shipping desktop binary through the Hermes engine.
7. **MNT-001** — the cheapest closure in the register: delete
   `normalized_output_dir`, decide whether the two dead settings are reserved or
   removed with migration, fix one line of `WINDOWS_BUILD_README.md`, retire
   `build_mac.sh`.

## Non-claims

No native run was performed on Windows or macOS. No physical microphone,
device-disconnect, disk-pressure, two-hour-recording, clipboard-history, or
OS-ACL behaviour was verified. No live OpenAI request was made. No Torch
training, evaluation, or model measurement was run. No exploit was demonstrated
for SEC-001, SEC-002, or SEC-003; the archive-expansion figure is a measured
allocation, not a crash. Every finding here rests on source review, on the
probes and commands quoted above, or on the public CI records linked above.
