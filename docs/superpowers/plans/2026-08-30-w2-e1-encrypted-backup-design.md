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
- No passphrase or derived key is intentionally persisted to settings, the
  restore journal, a sidecar, diagnostics, CLI JSON output, logs, files or
  Git. Decrypted v2 payload is written only to authenticated restore staging
  and its intended final live-data/settings destinations, never to the
  journal, recovery sidecar, diagnostics or logs. The existing v1 plaintext
  sidecar remains readable and behaviorally unchanged; v2 uses a distinct
  encrypted sidecar.

Non-goals (stay R1/R2 or external): cloud/team backup sync, key escrow or
recovery service, signed model/backup channels, secure memory zeroization
guarantees beyond best-effort bytearray handling (CPython cannot guarantee
this; recorded as a limitation), deniability, and forward secrecy.

## 2. Threat model

- Attacker reads a stored or copied `.voice-backup` file (lost drive, shared
  folder, cloud mirror the user made themselves). They must learn no private
  content and no source name/hash. The accepted metadata leakage is limited to
  format/KDF identifiers, random salt, opaque encrypted-member count and
  ciphertext/plaintext sizes. Avoiding size/count leakage would require
  padding and is outside R0.
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
manifest stays plaintext but authenticated; every private member becomes an
opaque `payload/<eight-digit-index>.enc` blob encrypted as a sequence of
independently authenticated chunks. `payload/00000000.enc` is an encrypted
index that maps opaque names to logical backup members. Source names and
managed SHA-256-based filenames never appear in the plaintext manifest or ZIP
member table.

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

- `create_backup(..., passphrase: str | None = None)` — `None` preserves the
  current v1 plaintext schema and behavior; a string produces v2. An empty
  string is rejected (`passphrase cannot be empty`).
- `verify_backup(path, *, passphrase: str | None = None)` — v1 ignores the
  parameter (behavior unchanged); v2 without a passphrase raises
  `ValueError("backup is encrypted; a passphrase is required")`.
- `restore_backup(path, data_root, *, settings_target=None,
  passphrase: str | None = None)` — same rule as verify.
- `recover_interrupted_restore(data_root, *, settings_target=None,
  passphrase: str | None = None)` — v1 remains deterministic and
  non-interactive. A v2 `swap_completed` journal with an encrypted settings
  sidecar returns `action="passphrase_required"` when no passphrase is
  supplied; with a passphrase it authenticates/decrypts that sidecar, applies
  settings, and finishes recovery. A v2 `staging_building` journal never needs
  a passphrase: it removes only its strictly validated incomplete staging
  directory and journal.

Backward compatibility: every v1 call site (CLI, GUI, tests) passes no
passphrase and observes the same v1 schema and behavior. No cross-run
byte-for-byte claim is made for timestamped ZIP output. Old application
versions reading a v2 archive fail at the existing manifest-version check with
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
  the call, is encoded once as exact UTF-8 bytes without Unicode normalization,
  and is never returned, logged, stored, or passed to the worker process.
- GUI startup may first receive `action="passphrase_required"`, then show one
  masked prompt and call recovery again. CLI startup reports the pending
  encrypted recovery concretely and prompts only in an interactive backup
  recovery command. Cancellation leaves the encrypted sidecar and journal
  intact; it never discards a usable recovery payload.

## 5. Backup v2 format (final contract)

Container: ZIP, written and verified under the unchanged `BACKUP_ZIP_BUDGET`
and `inspect_zip` protections. v2 members are stored with `ZIP_STORED`
(ciphertext does not compress; this also neutralizes compression-ratio
abuse of encrypted members).

### 5.1 `manifest.json` (plaintext, authenticated)

```json
{
  "version": 2,
  "encryption": {
    "algorithm": "AES-256-GCM-CHUNKED",
    "kdf": "argon2id",
    "kdf_params": {"iterations": 3, "memory_cost_kib": 65536, "lanes": 1},
    "salt_base64": "<16 random bytes>",
    "manifest_tag_base64": "<32 bytes>"
  },
  "index_member": "payload/00000000.enc",
  "members": {
    "payload/00000000.enc": {
      "sha256": "<hex of ciphertext>",
      "size": <ciphertext bytes>,
      "plaintext_size": <int>,
      "chunks": <int>
    },
    "payload/00000001.enc": {...}
  }
}
```

Rules:

- `manifest.json` is the only plaintext member. It carries no transcript
  text, settings values, logical member names, source hashes or key material —
  only the accepted metadata leakage listed in section 2.
- `payload/00000000.enc` decrypts to the versioned private index. The index
  contains `created_at`, `records`, `include_audio`, and an exact logical-name
  to opaque-name mapping for `transcripts.jsonl`, optional config members and
  sources. Logical names receive the existing path and fixed-member checks
  only after the index is authenticated. Opaque names must match
  `payload/[0-9]{8}.enc`, be unique and consecutive from zero, and the index
  may not map itself as a user payload. Its authenticated plaintext size is
  capped at 16 MiB before allocation/JSON parsing.
- `manifest_key = HKDF-SHA-256(master_key, salt=None, length=32,
  info=b"voice-studio-backup-v2-manifest-key")`.
- `manifest_tag_base64 = HMAC-SHA-256(manifest_key,
  b"voice-studio-backup-v2-manifest" || sha256(canonical_manifest_bytes))`
  where `canonical_manifest_bytes` is the manifest JSON serialized with
  `sort_keys=True`, `separators=(",", ":")`, and the
  `manifest_tag_base64` field set to the empty string. Creation and
  verification use `cryptography.hazmat.primitives.hmac.HMAC`; verification
  calls its constant-time `verify()` method.
- The ZIP member set must equal
  `{"manifest.json", *manifest["members"]}` exactly.
- KDF parameters are parsed under hard pre-derivation bounds (`iterations`
  1–10, `memory_cost_kib` 1024–262144, `lanes` 1–4, salt 16–32 bytes) to stop
  hostile resource requests, then v2 requires the exact supported profile
  `{iterations: 3, memory_cost_kib: 65536, lanes: 1}` and exactly 16 salt
  bytes. Algorithm agility or weaker accepted profiles require a future backup
  version; v2 never silently accepts them.

### 5.2 Key schedule

1. `master_key = Argon2id(salt=salt, length=32, iterations=3, lanes=1,
   memory_cost=65536).derive(passphrase_utf8)`. `Argon2id` was introduced in
   cryptography 44, while this 2026 design selects the reviewed current major
   range `cryptography>=50,<51`; version 43 is categorically insufficient.
   `"argon2id"` is the only v2 KDF. An unavailable algorithm is a concrete
   unsupported-runtime error and never triggers a silent or format fallback.
2. Per member: `member_key = HKDF-SHA-256(master_key, salt=None, length=32,
   info=b"voice-studio-backup-v2-member:" + member_name_utf8)`.
3. Chunks: plaintext is split into 1 MiB chunks. Chunk `i` (0-based) is
   encrypted as
   `AESGCM(member_key).encrypt(nonce, chunk, associated_data)` with
   `nonce = b"\x00\x00\x00\x00" + i.to_bytes(8, "big")` (unique per key by
   construction; no nonce storage needed) and
   `associated_data = member_name_ascii + b"\x00" + i.to_bytes(8, "big") +
   (b"\x01" if last_chunk else b"\x00")`.
   The last-chunk flag plus the manifest's `chunks`/`plaintext_size` fields
   make truncation and reordering hard errors.

Every member has at least one chunk: zero-length plaintext is represented by
one final empty chunk and its 16-byte GCM tag. Otherwise chunks are 1 MiB
except the final chunk. The manifest makes the concatenation parseable: each
non-final ciphertext chunk is exactly `1 MiB + 16 B`, and the final chunk is
the recorded remainder and is at least 16 B. Per-member ciphertext overhead
is exactly `16 * chunks` bytes. Per-archive overhead is the salt and manifest
tag inside `manifest.json`; no custom framing bytes exist.

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
  manifest-tag authentication, then index decryption/validation and streaming
  per-member chunk verification (hash + AEAD tag per chunk, chunk count and
  sizes against the manifest). The private index must form a one-to-one mapping
  over all non-index opaque members before any logical member is parsed.
- anything else → `unsupported backup version: <value>` (current behavior).

Public verification returns no passphrase or key material. Standalone
`verify_backup` decrypts/authenticates every chunk into a bounded discard
buffer. `restore_backup` shares the same private manifest/index verifier and
key context but authenticates each remaining payload exactly once while
streaming it into staging; it does not call standalone full verification and
then decrypt again. The private context never escapes the call.

Restore dispatches as follows:

- v1 → current restore path, unchanged.
- v2 authenticates the manifest and private index first. It then creates the
  restore directory and atomically writes a journal with
  `backup_version=2`, `stage="staging_building"`, and a strictly contained
  staging path **before the first plaintext byte is written**.
- Members are decrypted chunk-by-chunk directly into staging. Logical paths
  come only from the authenticated private index and pass the existing path,
  type and size checks. Plaintext never exists outside staging/live storage.
- Every payload tag/hash/size, restored record count and storage audit must be
  valid before the journal advances to `swap_started`; therefore no live root
  rename occurs until the complete archive is authenticated.
- Before swapping, staging receives an encrypted recovery directory named
  `.restore-settings-v2`. It contains the authenticated plaintext manifest
  plus the still-encrypted private index and only the opaque settings and
  dictionary members needed to finish settings recovery. It never contains a
  passphrase, key or decrypted settings value. It is created only when both a
  settings target and an encrypted settings member exist; otherwise the
  journal records the settings step complete and no sidecar is created.
- Recovery validates this deliberate subset with a sidecar-specific routine:
  authenticate the full manifest, verify the included ciphertext hashes,
  decrypt/authenticate the index, require exactly the index plus the mapped
  config members, and reject every extra file. It does not weaken the full
  archive's exact-member rule or parse an unauthenticated logical name.
- After staging is complete, audited, and has copied current `models/` and
  `exports/`, the existing journal is atomically advanced to `swap_started`
  with its resolved recovery path. The two renames and `swap_completed` stage
  then follow the current flow.
- The uninterrupted call applies its already-authenticated in-memory settings,
  removes `.restore-settings-v2`, marks settings complete and removes the
  journal. A crash after the swap leaves only encrypted recovery payload.

`RESTORE_JOURNAL_VERSION` stays 1 and `backup_version` accepts `{1, 2}`.
`staging_building` is valid only for backup version 2 with no recovery path.
Recovery of that stage validates containment and that the live data root was
never renamed, then removes the incomplete staging and journal. For
`swap_completed` with pending v2 settings, recovery without a passphrase
returns `action="passphrase_required"`; recovery with a passphrase authenticates
the manifest/index/config subset, applies settings, then removes the encrypted
sidecar and journal. Wrong passphrases and cancellation leave both intact.
The journal never records that a passphrase existed.

## 7. Secret-material prohibitions (enforceable rules)

The passphrase, master key and derived keys must never appear in:

- `settings.json` or any `Settings` field (no field is added);
- the restore journal (paths, versions, stages and counters only);
- the v2 restore sidecar, which contains only authenticated manifest metadata
  and original encrypted chunks. The existing v1 plaintext sidecar remains a
  version-1 compatibility path and is never reused for v2;
- diagnostics (`diagnostics --export` output is produced from Settings and
  store metadata and never receives the passphrase);
- CLI JSON results (result dicts gain no key or passphrase fields);
- logs/stderr (errors name the failing check, never the secret);
- Git (no test vectors with real passphrases; tests use synthetic ones).

Decrypted private payload is permitted only in bounded in-memory buffers,
authenticated staging and its intended final live data/settings files. It is
forbidden in the journal, v2 encrypted sidecar, diagnostics, CLI results,
logs/stderr and Git.

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
  ciphertext member limit is `plaintext_limit + 16 * max(1,
  ceil(plaintext_limit / 1 MiB))` and the manifest records both sizes.
  `BACKUP_ZIP_BUDGET`
  values are unchanged (they bound the container; ciphertext is incompressible
  and ratio checks apply to `ZIP_STORED` members as 1.0).
- The private index has its separate 16 MiB plaintext ceiling and is decrypted
  into bounded memory only after manifest authentication; the 20,000-member
  outer limit still applies before any KDF or decryption work.
- Free-space preflight: unchanged formula; `expanded_bytes` for v2 uses
  plaintext sizes from the authenticated manifest, revalidated during
  streaming decryption (a manifest that lies about sizes fails the AEAD or
  count check before the swap).
- Restore interruption: ordinary exceptions remove staging and its early
  journal while the live root is untouched. A hard process death leaves a
  `staging_building` journal that the next startup uses to remove only that
  strictly contained incomplete directory. A death after swap leaves an
  encrypted config sidecar and requires the passphrase to finish settings.
- Existing v1 journals/sidecars remain readable. Recovery directories are
  still never auto-deleted.

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

- `pyproject.toml`: add `cryptography>=50,<51` to `dependencies`; the Windows
  lock selects the reviewed 50.0.1 release available on the design date. The
  boundary is grounded in the official
  [Argon2id API documentation](https://cryptography.io/en/stable/hazmat/primitives/key-derivation-functions/)
  and [50.0.1 release metadata](https://pypi.org/project/cryptography/50.0.1/).
- `requirements-windows.lock`: add the exact reviewed `cryptography` release
  (plus any new transitive rows such as `pycparser` if the selected release
  requires it) in the same deliberate-review step the lock header describes.
- SBOM: `scripts/generate_sbom.py` consumes the lock, so the SBOM gains the
  new component automatically; the component-count assertions in the SBOM
  tests are updated in the same commit with the new deterministic artifact
  hash recorded in `VERIFICATION.md`.
- PyInstaller: extend the existing `collect_submodules` expression with
  `collect_submodules("cryptography")`; retain the pinned PyInstaller
  cryptography hook for native bindings and inspect build warnings. A bare
  top-level `"cryptography"` hidden import is not accepted as proof of a
  complete frozen runtime.
- Frozen probe: the runtime probe performs an in-process AES-GCM
  encrypt/decrypt round-trip and an Argon2id derive with test parameters, so
  a broken frozen native `cryptography` bundle fails the build gate.
- `scripts/build_windows.ps1` gains no new secret surface; artifact set and
  checksums are unchanged apart from the new SBOM hash.

## 11. RED test matrix (all fail on the untouched implementation)

Slice A — primitives (`voice_studio/backup_crypto.py`, new module):

1. Argon2id derive is deterministic for fixed salt, differs across salts,
   consumes exact non-normalized UTF-8 and fails concretely when the runtime
   lacks Argon2id; no scrypt fallback exists.
2. HKDF manifest/member keys are domain-separated, and member keys differ per
   opaque member name.
3. Chunked encrypt/decrypt round-trip over sizes 0, 1, 1 MiB, 1 MiB + 1,
   and a streaming multi-chunk payload; zero bytes produce one authenticated
   final chunk and every non-final ciphertext chunk is `1 MiB + 16 B`.
4. Wrong key, flipped ciphertext byte, flipped tag byte, reordered chunks,
   truncated final chunk and wrong chunk count each raise the concrete
   authentication error.
5. Manifest tag verifies through `cryptography` HMAC; any canonical manifest
   byte change fails it.

Slice B — format and create/verify:

6. v1 create/verify/restore behavior and schema are unchanged with no
   passphrase; no cross-run byte-for-byte archive claim is made because v1
   already contains timestamps and ZIP metadata.
7. `create_backup(passphrase=...)` writes version 2, consecutive opaque
   `payload/NNNNNNNN.enc` names only besides the manifest, `ZIP_STORED`
   encryption members, and a manifest/ZIP table without transcript content,
   logical member names, source names or managed source hashes. The encrypted
   index is a bijection over all remaining opaque members.
8. Verify v2 without passphrase → the concrete "passphrase is required"
   error; with wrong passphrase → authentication error; with the right one →
   PASS with correct record count.
9. Tamper cases: modified manifest byte, modified ciphertext byte, truncated
   archive, removed member, added member, renamed member — each a hard error,
   none parsed as plaintext.
10. Budget cases: hostile KDF parameters, oversized ciphertext member,
    member-count overflow, oversized private index, false plaintext size and
    logical fixed-member limits are rejected before unsafe allocation or
    live-state mutation.
11. The pinned public-signature test covers create/verify/restore and
    `recover_interrupted_restore`; the migration note and v1 defaults land in
    the same commit.

Slice C — restore and recovery:

12. v2 restore round-trip: records, settings payload, dictionary and audio
    land correctly; `models/` and `exports/` survive; the user original is
    untouched.
13. v2 restore with wrong passphrase changes nothing on disk (live root,
    journal, staging all absent afterwards).
14. A simulated hard death after the first decrypted chunk leaves a
    `staging_building` journal; startup deletes only the contained incomplete
    staging and journal while preserving the live root and user original.
15. Swap/recovery tests cover `swap_started`, `swap_completed`, encrypted
    `.restore-settings-v2`, no-passphrase `passphrase_required`, wrong
    passphrase/cancel preservation, correct-passphrase completion, and no
    plaintext settings/dictionary in journal or sidecar.
16. No plaintext private payload remains outside staging/live storage after a
    decryption failure; normal failures remove staging and the early journal.

Slice D — secret hygiene and packaging:

17. After a full v2 cycle, the passphrase bytes and master-key bytes appear in
    none of: settings file, restore journal, sidecar, diagnostics export, CLI
    JSON output, captured stderr, or the Git-tracked tree. Known settings and
    dictionary plaintext markers do not appear in the v2 journal or encrypted
    recovery sidecar.
18. `cryptography` is importable in a frozen-style probe and the AES-GCM /
    Argon2id round-trips pass (source-level probe; packaged probe runs in the
    R0.10 gate).
19. SBOM regeneration with the updated lock is deterministic and contains the
    new component.
20. Structural contract is deterministic (member set, manifest schema, chunk
    sizing) without requiring deterministic ciphertext (random salt per
    archive).

## 12. Implementation slices (each sized for one turn)

1. **Slice 0 — dependency/provenance boundary**: add
   `cryptography>=50,<51`, update the exact Windows lock, deterministic SBOM
   artifact/assertions and PyInstaller submodule collection, then prove source
   import and the existing gates. No backup behavior changes.
2. **Slice A — crypto primitives**: implement `backup_crypto.py` (Argon2id,
   domain-separated HKDF, chunked AEAD, manifest tag) and tests 1–5 against the
   already-declared dependency. No existing backup API changes.
3. **Slice B — v2 create + verify dispatch**: opaque manifest/private index,
   version dispatch, signature migration note, tests 6–11 and budgets.
4. **Slice C1 — v2 data restore**: early `staging_building` journal, streaming
   decrypt into staging, audit/local-state preservation and tests 12–14.
5. **Slice C2 — encrypted settings recovery + CLI**: encrypted sidecar,
   passphrase-aware recovery and CLI `getpass` flow, tests 15–16.
6. **Slice D — GUI, hygiene and final packaging proof**: dialog
   checkbox/prompt, i18n, secret-scan test 17, frozen cryptography probe and
   tests 18–20, Help/README/SECURITY/ARCHITECTURE alignment, and security
   self-review.

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
  containing W2-E1. Before downgrading, any v2 restore journal must be finished
  or safely discarded by a W2-E1 build. Older builds reject `backup_version=2`
  or `staging_building` and leave the journal/staging untouched; they never
  guess or delete it. No user-data schema migration is required.
- Default remains plaintext v1 on create; encryption is explicit opt-in,
  matching the local/private-by-default posture (no surprise behavior change
  for existing users or scripts).

## 14. Known limitations (to be recorded, never silently promoted)

- CPython cannot guarantee secure memory erasure of the passphrase `str` or
  derived keys; best-effort scoping only.
- Passphrase strength is user-controlled; the KDF raises offline-attack cost
  but weak passphrases remain weak.
- The encrypted container leaks opaque member count and ciphertext/plaintext
  sizes. It does not leak logical filenames, managed source hashes, record
  count or content. Padding to hide count/size is outside R0.
- Successful AEAD/HMAC verification proves possession of the passphrase-derived
  key and archive integrity; it is not a publisher signature or identity
  attestation.
- `cryptography` is a new native dependency; macOS source runners install it
  via the project extras, and the frozen Windows runtime is proven by the
  probe in the R0.10 gate. No physical-device or packaged acceptance is
  claimed by this design.
