# Desktop data safety — design

Status: **PROPOSED / NOT AUTHORIZED FOR IMPLEMENTATION**. This is a documentation-only
design artifact from the read-only audit. It may be implemented only after a later,
explicit `DESIGN -> USER APPROVAL -> IMPLEMENTATION` gate.

Scope: first implementation increment from Phase 0. It closes `COR-002`, `COR-004`, `PRV-003`, `REL-001` and the microphone-temp part of `PRV-001`. It does not address Hermes overlap, cancellable file import, native media isolation or archive limits; those remain later sequential increments.

## Design choice

Use a compatibility-first patch inside the existing desktop architecture.

Alternatives rejected for this increment:

- autosaving every editor mutation: increases database writes and changes the meaning of explicit Save/undo;
- a full Tk application/state-machine rewrite: better long-term separation, but unnecessary risk and scope for the customer-data defects;
- retaining automatic clipboard as the default: conflicts with the privacy-first product promise.

## Settings contract

`Settings.from_dict` will validate JSON value types before constructing the dataclass:

- string fields accept strings only;
- `auto_copy`, `insert_to_active_app` and `offline_only` accept JSON booleans only;
- `task_timeout_seconds` accepts an integer but rejects booleans;
- unknown fields remain ignored for forward/backward compatibility;
- the existing legacy hotkey migration remains;
- every type failure is a `ValueError` naming the field and expected type, so GUI recovery and CLI error handling work consistently.

`Settings.auto_copy` changes from `True` to `False`. An existing settings file that explicitly stores `true` remains enabled. Missing/new settings receive the safe default.

The Settings dialog labels auto-copy as an OS clipboard/history/sync boundary. Explicit Copy remains available and does not add another confirmation.

## Dirty editor contract

The persisted snapshot is the current transcript’s `corrected_text` plus bold/italic formatting. Dirty state is computed from the live editor at transition time; it is not dependent solely on Tk’s modified flag.

Before history navigation or application close:

- unchanged: continue immediately;
- dirty: display one `Save / Discard / Cancel` prompt;
- Save: persist text and formatting; continue only on success;
- Discard: continue without persistence;
- Cancel: keep the current transcript/editor and abort navigation/close;
- save failure: show the concrete error and abort transition.

If a history selection is cancelled, restore the visual selection to the current transcript. Closing while transcription/recording is active still performs the existing cancellation only after dirty-state permission succeeds.

New transcription results remain authoritative transitions. The app will prompt if a result arrives while the visible current transcript has unsaved edits, then either save/discard before displaying the result or keep the completed result in history while leaving the editor unchanged on Cancel. No raw text is modified.

## Recording architecture

Replace unbounded in-memory accumulation with a bounded producer/consumer recorder:

1. The application creates a private temporary WAV path before capture and records ownership in a pending-temp set.
2. `AudioRecorder.start(path)` opens a dedicated writer thread.
3. The sounddevice callback receives fixed 100 ms blocks and performs only a bounded non-blocking byte enqueue.
4. The writer thread streams frames to the WAV file.
5. A bounded queue caps callback backlog; overflow/dropout is counted and surfaced as a warning before transcription.
6. A two-hour safety ceiling stops capture automatically. The constant is explicit, tested and shown to the user; it is not presented as an engineering standard.
7. `stop()` drains/joins the writer, closes a valid WAV and returns capture statistics.
8. `cancel()` stops the stream/writer and removes the partial file.

Thread/writer failures are stored and raised by `stop`; a corrupt or empty recording is not sent to transcription. The app polls `limit_reached` and stops continuous recording safely.

## Temporary-file ownership

The app tracks every microphone temp file from creation until the transcription event consumes it. Success, failure and cancellation remove the file. Application close first stops/cancels model and recorder work, then removes all pending microphone files. Cleanup is idempotent with `missing_ok=True`.

The temporary file is created with restrictive permissions where supported. The implementation does not claim secure deletion from SSD/filesystem snapshots.

## Error and UX behavior

- Type errors: one recoverable settings message, no traceback-only GUI failure.
- Capture status/queue overflow: stop capture, warn that audio may be incomplete, and require explicit confirmation before transcription; default is not to process degraded audio.
- Duration ceiling: automatic stop with a specific two-hour-limit message.
- Writer/file failure: delete partial temp and show the concrete microphone error.
- Explicit clipboard copy: status confirms the action.
- Automatic copy, when an existing user keeps it enabled: settings warning explains that OS history/sync may retain text.

## Test design

Regression-first tests:

- settings wrong-type matrix for every field group, including `bool` as timeout;
- missing `auto_copy` receives `False`, explicit `true` is preserved, round-trip remains stable;
- pure dirty-state comparison for text and formatting;
- Save/Discard/Cancel/save-error transition behavior with lightweight fake widgets/store/messagebox, avoiding a physical display;
- automatic result transition cannot silently overwrite dirty editor content;
- fake sounddevice stream tests bounded queue, streamed WAV, status/overflow, two-hour limit, stop and cancel;
- temp ownership tests for success, processing error, job cancellation and close;
- existing source-contract tests updated only where real behavioral coverage is not possible.

All existing unit tests must pass. Fresh `compileall`, Ruff, build and dependency checks run where available. Manual follow-up covers real microphone/hotkey and Windows/macOS close behavior; these cannot be claimed from fakes.

## Acceptance gate

- malformed typed settings never raise unintended `TypeError`/`AttributeError`;
- no unsaved editor transition occurs without Save/Discard/Cancel handling;
- new installs do not automatically copy transcripts;
- recording memory is bounded independently of duration;
- capture degradation is visible and not processed without confirmation;
- microphone temp files are removed on every modeled terminal path;
- original user media and immutable `raw_text` invariants remain unchanged;
- no new cloud upload, telemetry, dependency or schema migration is introduced.
