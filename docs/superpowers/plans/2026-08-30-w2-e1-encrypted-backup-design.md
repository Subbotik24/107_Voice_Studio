# W2-E1 encrypted backup v2 — security design

Status date: 2026-08-30. Base commit: `600150b` (local `main`, unpushed).
This document is the final pre-implementation contract for the encrypted
backup increment. It is a design-only deliverable: no production code,
dependency, lock or test change is part of this commit.

Scope sources: `docs/superpowers/specs/2026-08-30-voice-studio-r0-completion-design.md`
§ R0.9, `docs/superpowers/plans/2026-08-30-r0-continuation-handoff.md`
increment 3, and `docs/superpowers/plans/2026-08-28-completion-roadmap.md`
§ W2-E1.

## 1. Goals and non-goals

Goals:

- A user can create an encrypted backup whose private payloads (transcripts,
  settings, dictionary, managed audio) are unreadable without the passphrase.
- Backup v1 archives remain readable and behaviorally unchanged forever.
- Wrong passphrase or any tampering is a hard, concrete error with no
  plaintext fallback.
- No passphrase, derived key or plaintext private payload reaches settings,
  the restore journal, the restore sidecar, diagnostics, CLI JSON output,
  logs, temporary files outside restore staging, or Git.

Non-goals (stay R1/R2 or external): cloud/team backup sync, key escrow or
recovery service, signed model/backup channels, secure memory zeroization
guarantees beyond best-effort bytearray handling (CPython cannot guarantee
this; recorded as a limitation), deniability, and forward secrecy.

## 2. Threat model

- Attacker reads a stored or copied `.voice-backup` file (lost drive, shared
  folder, cloud mirror the user made themselves). They must learn nothing
  about transcript content, settings, dictionary, or audio.
- Attacker modifies the archive. Verification must fail concretely; restore
  must never partially apply tampered plaintext.
- Attacker is a same-account local process: outside scope (that actor already
  reads the live data root), consistent with the existing recorder/storage
  threat notes in `SECURITY.md`.
- Passphrase strength is the user's responsibility; the KDF parameters below
  raise the offline-guessing cost but cannot fix a weak passphrase. The UI
  states this plainly.

## 3. Envelope options compared

### Option A — one-shot AEAD per member (AESGCM/Fernet over whole members)

Each private ZIP member is replaced by one `AESGCM.encrypt()` or Fernet blob.

- Pros: single standard call per member; minimal assembly.
- Cons: `cryptography`'s one-shot AEAD APIs buffer the entire plaintext and
  ciphertext in memory. The current member budget allows 2 GiB audio members
  and 512 MiB `transcripts.jsonl`; a restore would need >4 GiB RAM for one
  member. This breaks the streaming budget contract and the 8 GiB total
  budget on realistic machines. Fernet additionally is AES-CBC + HMAC, an
  older construction with no streaming API either.
- Verdict: rejected on memory/budget grounds.

### Option B — whole-container stream encryption

Wrap the entire existing v1 ZIP as one encrypted byte stream with a small
plaintext header (magic, version, KDF salt). Decrypt produces a plaintext
ZIP that the existing `inspect_zip` pipeline then handles unchanged.

- Pros: maximum code reuse; the whole member-level protection suite runs
  verbatim on the plaintext container.
- Cons: the plaintext ZIP must exist somewhere for `zipfile` to parse it.
  `zipfile` requires a seekable file, so restore would write the full
  plaintext container to a temporary file. A crash, forced termination or an
  error path then leaves the complete plaintext backup on disk — exactly the
  residue this increment forbids. In-memory parsing (`io.BytesIO`) reimports
  Option A's memory blowup at container scale (8 GiB budget).
- Verdict: rejected on plaintext-residue grounds.

### Option C — in-ZIP envelope with chunked AEAD per member (CHOSEN)

The v2 archive is still a ZIP container with the same safety surface. The
manifest stays plaintext but authenticated; every private member becomes a
`<name>.enc` blob encrypted as a sequence of independently authenticated
chunks.

- Pros: streaming with a 1 MiB working set; no plaintext temporary files
  outside restore staging; every existing ZIP/path/reparse/budget protection
  keeps running on the outer container; v1/v2 dispatch is a single manifest
  version read; truncation, reordering and per-chunk tampering are all
  detectable with standard primitives only.
- Cons: the chunk framing is an assembly of standard primitives (HKDF +
  AES-GCM + HMAC), so it must be specified exactly — that specification is
  section 5 and is pinned by the structural test matrix in section 10.
- Verdict: selected. This is the final contract.

## 4. Public API and passphrase boundary

Existing signatures pinned by `test_public_backup_signatures_are_unchanged`:

- `verify_backup(path)`
- `restore_backup(path, data_root, *, settings_target)`
- `create_backup(store, destination, *, settings_file=None, include_audio=True)`

Contract change (migration note required, and the pinned signature test is
updated in the same commit):

- `create_backup(..., passphrase: str | None = None)` — `None` produces a v1
  plaintext archive exactly as today; a string produces v2. An empty string is
  rejected (`passphrase cannot be empty`).
- `verify_backup(path, *, passphrase: str | None = None)` — v1 ignores the
  parameter (behavior unchanged); v2 without a passphrase raises
  `ValueError("backup is encrypted; a passphrase is required")`.
- `restore_backup(path, data_root, *, settings_target=None,
  passphrase: str | None = None)` — same rule as verify.

Backward compatibility: every v1 call site (CLI, GUI, tests) passes no
passphrase and observes byte-identical behavior. Old application versions
reading a v2 archive fail at the existing manifest-version check with
`unsupported backup version: 2` — a concrete error, never a misparsed file.

Passphrase acquisition:

- CLI `backup create`: `--encrypt` flag only; the passphrase is read with
  `getpass.getpass()` twice (confirmation) on a terminal. It is never accepted
  as a command-line argument (shell history/process list exposure). A
  non-interactive stdin raises a concrete error.
- CLI `backup verify` / `backup restore` on a v2 archive: detect the manifest
  version first, then prompt once with `getpass.getpass()`. v1 never prompts.
- GUI backup dialog: an "Encrypt with passphrase" checkbox; on create, a
  masked `simpledialog.askstring(show="*")` pair with confirmation; on
  verify/restore of a v2 archive, one masked prompt. The dialog text states
  that a lost passphrase makes the backup unrecoverable.
- The passphrase string lives only as a local variable for the duration of
  the call, is derived into key material once, and is never returned, logged,
  stored, or passed to the worker process.

## 5. Backup v2 format (final contract)

Container: ZIP, written and verified under the unchanged `BACKUP_ZIP_BUDGET`
and `inspect_zip` protections. v2 members are stored with `ZIP_STORED`
(ciphertext does not compress; this also neutralizes compression-ratio
abuse of encrypted members).

### 5.1 `manifest.json` (plaintext, authenticated)

```json
{
  "version": 2,
  "created_at": "<UTC ISO-8601>",
  "records": <int>,
  "include_audio": <bool>,
  "encryption": {
    "algorithm": "AES-256-GCM-CHUNKED",
    "kdf": "argon2id",
    "kdf_params": {"time_cost": 3, "memory_cost_kib": 65536, "parallelism": 1},
    "salt_base64": "<16 random bytes>",
    "manifest_tag_base64": "<32 bytes>"
  },
  "members": {
    "transcripts.jsonl.enc": {
      "sha256": "<hex of ciphertext>",
      "size": <ciphertext bytes>,
      "plaintext_size": <int>,
      "chunks": <int>
    },
    "config/settings.json.enc": {...},
    "config/dictionary.json.enc": {...},
    "sources/<name>.enc": {...}
  }
}
```

Rules:

- `manifest.json` is the only plaintext member. It carries no transcript
  text, no settings values, no key material — only the salt, KDF parameters,
  ciphertext hashes/sizes and the manifest tag.
- `manifest_tag_base64 = HMAC-SHA-256(master_key,
  b"voice-studio-backup-v2-manifest" || sha256(canonical_manifest_bytes))`
  where `canonical_manifest_bytes` is the manifest JSON serialized with
  `sort_keys=True`, `separators=(",", ":")`, and the
  `manifest_tag_base64` field set to the empty string. Verification
  recomputes and compares with `hmac.compare_digest`.
- Every manifest member name must end with `.enc`, and the ZIP member set
  must equal `{"manifest.json", *manifest["members"]}` exactly (same rule as
  v1, plus the suffix rule).
- KDF parameters are bounded on load: `time_cost` 1–10,
  `memory_cost_kib` 1024–262144, `parallelism` 1–4, salt length 16–32 bytes.
  Out-of-range values are rejected before derivation, so a hostile manifest
  cannot trigger a memory-exhaustion KDF.

### 5.2 Key schedule

1. `master_key = Argon2id(passphrase, salt, time_cost=3,
   memory_cost=64 MiB, parallelism=1, length=32)`.
   `argon2id` is available in `cryptography >= 43`. If the pinned version
   ever lacks it, the documented fallback is
   `scrypt(length=32, n=2**15, r=8, p=1)` recorded as `"kdf": "scrypt"` —
   no other KDF is accepted.
2. Per member: `member_key = HKDF-SHA-256(master_key, salt=None, length=32,
   info=b"voice-studio-backup-v2-member:" + member_name_utf8)`.
3. Chunks: plaintext is split into 1 MiB chunks. Chunk `i` (0-based) is
   encrypted as
   `AESGCM(member_key).encrypt(nonce, chunk, associated_data)` with
   `nonce = b"\x00\x00\x00\x00" + i.to_bytes(8, "big")` (unique per key by
   construction; no nonce storage needed) and
   `associated_data = member_name_utf8 + b"\x00" + i.to_bytes(8, "big") +
   b"\x01" if last_chunk else b"\x00"`.
   The last-chunk flag plus the manifest's `chunks`/`plaintext_size` fields
   make truncation and reordering hard errors.

Per-member ciphertext overhead is exactly `16 * chunks` bytes (the GCM tag
per chunk). Per-archive overhead is the salt (16 B) and tag (32 B) inside the
manifest. No other framing bytes exist.

### 5.3 Error contract

- v2 archive, `passphrase=None`:
  `ValueError("backup is encrypted; a passphrase is required")`.
- Wrong passphrase → manifest tag mismatch:
  `ValueError("backup authentication failed: wrong passphrase or corrupted manifest")`.
- Tampered ciphertext / truncated archive / modified chunk →
  `ValueError("backup member authentication failed: <name>")` (from the GCM
  tag), or the existing ZIP/manifest structural errors.
- After any authentication failure the member bytes are never parsed as
  plaintext. There is no code path from a v2 authentication failure into v1
  parsing; dispatch is keyed on the manifest version only.

## 6. Version dispatch

`verify_backup` reads `manifest.json` after the unchanged ZIP budget and
member-safety checks, then dispatches:

- `version == 1` → the current verification path, unchanged.
- `version == 2` → KDF-parameter validation, passphrase requirement,
  manifest-tag authentication, then streaming per-member chunk verification
  (hash + AEAD tag per chunk, chunk count and sizes against the manifest).
- anything else → `unsupported backup version: <value>` (current behavior).

`restore_backup` always calls `verify_backup` first (unchanged), then:

- v1 → current restore path, unchanged.
- v2 → restore staging is built by *streaming decryption*: each member is
  decrypted chunk-by-chunk and written directly into the restore staging
  directory (`transcripts.jsonl` into staging, sources into
  `staging/sources/`), so plaintext exists only inside staging. Staging is
  removed by the existing `finally: shutil.rmtree(temporary)` on every
  failure path, and the restore journal/swap/recovery flow then operates on
  staging exactly as today.

`RESTORE_JOURNAL_VERSION` stays 1, but the journal's `backup_version` field
accepts `{1, 2}`; the journal never records that a passphrase existed.

## 7. Secret-material prohibitions (enforceable rules)

The passphrase, master key, member keys and decrypted private payloads must
never appear in:

- `settings.json` or any `Settings` field (no field is added);
- the restore journal (paths/counters only — unchanged);
- the restore sidecar (it carries the restored settings payload, which is
  backup *content*, never key material; unchanged);
- diagnostics (`diagnostics --export` output is produced from Settings and
  store metadata and never receives the passphrase);
- CLI JSON results (result dicts gain no key or passphrase fields);
- logs/stderr (errors name the failing check, never the secret);
- Git (no test vectors with real passphrases; tests use synthetic ones).

Regression tests scan the on-disk artifacts of a full v2
create/verify/restore cycle for the exact passphrase and for derived-key
bytes (section 10).

Memory handling: the passphrase is converted into the master key as soon as
possible; intermediate chunk buffers are 1 MiB and are overwritten by
reuse. CPython cannot guarantee zeroization of immutable `str`; this is
recorded as a known limitation in `SECURITY.md`, not hidden.

## 8. Budgets, streaming and temporary files

- Working set during create/verify/restore: one 1 MiB plaintext chunk plus
  its ciphertext and the ZIP stream buffer — independent of archive size.
- `_FIXED_MEMBER_LIMITS` stay defined on plaintext sizes; for v2 the
  ciphertext member limit is `plaintext_limit + 16 * ceil(plaintext_limit /
  1 MiB) + 16` and the manifest records both sizes. `BACKUP_ZIP_BUDGET`
  values are unchanged (they bound the container; ciphertext is incompressible
  and ratio checks apply to `ZIP_STORED` members as 1.0).
- Free-space preflight: unchanged formula; `expanded_bytes` for v2 uses
  plaintext sizes from the authenticated manifest, revalidated during
  streaming decryption (a manifest that lies about sizes fails the AEAD or
  count check before the swap).
- Restore interruption: a process death mid-decrypt leaves staging, which
  `recover_interrupted_restore`/the next restore `finally` handles exactly as
  today; no decrypted plaintext survives outside staging; the live data root
  is untouched until the journaled swap.
- The restore journal, sidecar and `*.recovery-*` flows are byte-compatible;
  recovery directories are still never auto-deleted.

## 9. Preserved invariants

- The user original media file is never deleted (backup/restore never touch
  it; unchanged).
- `models/` and `exports/` machine-local state transfer into staging before
  the swap (`_copy_local_restore_state`) is unchanged for v2.
- Restore keeps the storage audit gate before the swap; v2 adds decryption
  before staging build, which cannot weaken it.
- All existing ZIP protections apply to the outer container unchanged:
  bounded EOCD/ZIP64 preflight, member-count/size budgets, duplicate-member
  rejection, absolute/`..`/backslash member rejection, and the no-reparse
  local-state copy checks.
- UI keeps no knowledge of engine internals; the backup dialog learns only a
  checkbox and a masked prompt.

## 10. Dependency, packaging and provenance changes

- `pyproject.toml`: add `cryptography>=43,<47` to `dependencies`.
- `requirements-windows.lock`: add the exact reviewed `cryptography` release
  (plus any new transitive rows such as `pycparser` if the selected release
  requires it) in the same deliberate-review step the lock header describes.
- SBOM: `scripts/generate_sbom.py` consumes the lock, so the SBOM gains the
  new component automatically; the component-count assertions in the SBOM
  tests are updated in the same commit with the new deterministic artifact
  hash recorded in `VERIFICATION.md`.
- PyInstaller: add `"cryptography"` to `hiddenimports` in
  `packaging/voice_studio.spec` (explicit collection; the frozen import must
  not depend on hook discovery).
- Frozen probe: the runtime probe performs an in-process AES-GCM
  encrypt/decrypt round-trip and an Argon2id derive with test parameters, so
  a broken frozen `cryptography`/cffi bundle fails the build gate.
- `scripts/build_windows.ps1` gains no new secret surface; artifact set and
  checksums are unchanged apart from the new SBOM hash.

## 11. RED test matrix (all fail on the untouched implementation)

Slice A — primitives (`voice_studio/backup_crypto.py`, new module):

1. Argon2id derive is deterministic for fixed salt and differs across salts.
2. HKDF member keys differ per member name.
3. Chunked encrypt/decrypt round-trip over sizes 0, 1, 1 MiB, 1 MiB + 1,
   and a streaming multi-chunk payload.
4. Wrong key, flipped ciphertext byte, flipped tag byte, reordered chunks,
   truncated final chunk and wrong chunk count each raise the concrete
   authentication error.
5. Manifest tag verifies; any manifest byte change fails it.

Slice B — format and create/verify:

6. v1 create/verify/restore behavior is byte-identical with no passphrase
   (existing suite, unchanged).
7. `create_backup(passphrase=...)` writes version 2, `.enc` members only
   besides the manifest, `ZIP_STORED` encryption members, and a manifest
   without any transcript/settings/dictionary plaintext.
8. Verify v2 without passphrase → the concrete "passphrase is required"
   error; with wrong passphrase → authentication error; with the right one →
   PASS with correct record count.
9. Tamper cases: modified manifest byte, modified ciphertext byte, truncated
   archive, removed member, added member, renamed member — each a hard error,
   none parsed as plaintext.
10. Budget cases: oversized ciphertext member, member-count overflow and the
    fixed-member ciphertext limits are enforced exactly as v1 enforces its
    own.
11. The pinned public-signature test is updated to the new signatures in the
    same commit (migration note), and v1 callers pass with defaults.

Slice C — restore and recovery:

12. v2 restore round-trip: records, settings payload, dictionary and audio
    land correctly; `models/` and `exports/` survive; the user original is
    untouched.
13. v2 restore with wrong passphrase changes nothing on disk (live root,
    journal, staging all absent afterwards).
14. Simulated process death between swap steps on a v2 restore recovers via
    the journal exactly like v1 (completed / rolled_back paths).
15. No plaintext private payload remains in any temporary location after a
    decryption failure mid-restore.

Slice D — secret hygiene and packaging:

16. After a full v2 cycle, the passphrase bytes and master-key bytes appear in
    none of: settings file, restore journal, sidecar, diagnostics export, CLI
    JSON output, captured stderr, or the Git-tracked tree.
17. `cryptography` is importable in a frozen-style probe and the AES-GCM /
    Argon2id round-trips pass (source-level probe; packaged probe runs in the
    R0.10 gate).
18. SBOM regeneration with the updated lock is deterministic and contains the
    new component.
19. Structural contract is deterministic (member set, manifest schema, chunk
    sizing) without requiring deterministic ciphertext (random salt per
    archive).

## 12. Implementation slices (each sized for one turn)

1. **Slice A — crypto primitives module**: `backup_crypto.py` (KDF, HKDF,
   chunked AEAD, manifest tag) with tests 1–5. No existing file changes.
2. **Slice B — v2 create + verify dispatch**: manifest schema, `.enc`
   members, version dispatch, signature migration note, tests 6–11, budgets.
3. **Slice C — v2 restore + recovery**: streaming decrypt into staging,
   journal `backup_version` acceptance, CLI `getpass` flow, tests 12–15.
4. **Slice D — GUI + hygiene + packaging**: dialog checkbox/prompt, i18n,
   secret-scan test 16, probe test 17, lock/SBOM/spec updates, tests 18–19,
   Help/README/SECURITY/ARCHITECTURE alignment, security self-review.

Each slice ends with its focused tests green; the full source gate runs once
after slice D's final production change, before the R0.10 gate.

## 13. Migration and rollback

- Migration: none required for user data. v1 archives remain readable by new
  code; v2 archives fail concretely on old code. The only contract change is
  the additive `passphrase` parameter (keyword-only), recorded here and in
  the commit message as the migration note.
- Mixed environments: a user restoring a v2 archive must run a build that
  includes W2-E1; the error message states the archive version explicitly.
- Rollback: reverting the slice commits restores v1-only behavior; v2
  archives created meanwhile remain intact on disk and readable by any build
  containing W2-E1. No journal/schema migration is needed because the journal
  format only widens the accepted `backup_version` set.
- Default remains plaintext v1 on create; encryption is explicit opt-in,
  matching the local/private-by-default posture (no surprise behavior change
  for existing users or scripts).

## 14. Known limitations (to be recorded, never silently promoted)

- CPython cannot guarantee secure memory erasure of the passphrase `str` or
  derived keys; best-effort scoping only.
- Passphrase strength is user-controlled; the KDF raises offline-attack cost
  but weak passphrases remain weak.
- `cryptography` is a new native dependency; macOS source runners install it
  via the project extras, and the frozen Windows runtime is proven by the
  probe in the R0.10 gate. No physical-device or packaged acceptance is
  claimed by this design.
