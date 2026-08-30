"""Tests for encrypted backup v2 verification (W2-E1 Slice B2).

Contract source:
docs/superpowers/plans/2026-08-30-w2-e1-encrypted-backup-design.md
sections 5-6 and the Slice B2 task brief. Tamper helpers rebuild
synthetic archives and recompute the manifest tag only when the layer
under test sits after HMAC authentication; production validation is
never weakened for fixtures.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import inspect
import io
import json
import zipfile
from pathlib import Path

import pytest

from voice_studio import backup_crypto
from voice_studio.backup import create_backup, verify_backup
from voice_studio.backup_crypto import CHUNK_SIZE
from voice_studio.models import Transcript
from voice_studio.storage import LocalStore

PASSPHRASE = "synthetic slice-b2 passphrase"
WRONG = "synthetic wrong passphrase"
REQUIRED_ERROR = "backup is encrypted; a passphrase is required"
MANIFEST_ERROR = "backup authentication failed: wrong passphrase or corrupted manifest"


def _transcript(item_id: str, source_hash: str, source_path: str) -> Transcript:
    return Transcript(
        id=item_id,
        created_at="2026-07-27T00:00:00+00:00",
        source_name="safe.wav",
        source_sha256=source_hash,
        source_path=source_path,
        language="uk",
        engine="fixture",
        model="fixture",
        raw_text=f"raw {item_id}",
        corrected_text=f"corrected {item_id}",
    )


def _seed(tmp_path: Path, make_wav, *, records: int = 2):
    store = LocalStore(tmp_path / "store")
    for index in range(records):
        original = make_wav(tmp_path / f"original-{index}.wav")
        managed, digest = store.import_source(original)
        store.save(_transcript(f"rec-{index}", digest, str(managed)))
    return store


def _make_v2(tmp_path: Path, make_wav) -> Path:
    store = _seed(tmp_path, make_wav)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, passphrase=PASSPHRASE)
    return backup


def _load_archive(path: Path):
    with zipfile.ZipFile(path) as archive:
        compression = {info.filename: info.compress_type for info in archive.infolist()}
        blobs = {name: archive.read(name) for name in compression}
    return compression, blobs


def _store_archive(path: Path, compression: dict[str, int], blobs: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in blobs.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression[name]
            archive.writestr(info, data)


def _keys(manifest: dict, passphrase: str):
    salt = base64.b64decode(manifest["encryption"]["salt_base64"])
    master_key = backup_crypto.derive_master_key(passphrase, salt)
    return master_key, backup_crypto.derive_manifest_key(master_key)


def _retag(manifest: dict, manifest_key: bytes) -> dict:
    manifest = copy.deepcopy(manifest)
    manifest["encryption"]["manifest_tag_base64"] = ""
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest["encryption"]["manifest_tag_base64"] = base64.b64encode(
        backup_crypto.compute_manifest_tag(manifest_key, canonical)
    ).decode("ascii")
    return manifest


def _tampered_archive(
    tmp_path: Path,
    source: Path,
    *,
    manifest_mutate=None,
    blob_mutate=None,
    retag_with: str | None = None,
    name: str = "tampered.voice-backup",
) -> Path:
    """Rebuild an archive, optionally re-tagging after the mutation.

    ``retag_with`` recomputes a valid HMAC after the change so tests can
    reach validation layers that sit after manifest authentication.
    """
    compression, blobs = _load_archive(source)
    manifest = json.loads(blobs["manifest.json"])
    if manifest_mutate is not None:
        manifest_mutate(manifest, blobs)
    if blob_mutate is not None:
        blob_mutate(manifest, blobs)
    for blob_name in blobs:
        compression.setdefault(blob_name, zipfile.ZIP_STORED)
    if retag_with is not None:
        _master, manifest_key = _keys(manifest, retag_with)
        manifest = _retag(manifest, manifest_key)
    blobs["manifest.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    target = tmp_path / name
    _store_archive(target, compression, blobs)
    return target


def _rewrite_index(
    manifest: dict, blobs: dict, master_key: bytes, new_index
) -> None:
    """Re-encrypt the private index and refresh its manifest metadata."""
    name = manifest["index_member"]
    plaintext = (
        new_index
        if isinstance(new_index, bytes)
        else json.dumps(new_index, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    member_key = backup_crypto.derive_member_key(master_key, name)
    out = io.BytesIO()
    plaintext_size, chunks = backup_crypto.encrypt_member(
        name, member_key, io.BytesIO(plaintext), out
    )
    ciphertext = out.getvalue()
    blobs[name] = ciphertext
    manifest["members"][name] = {
        "sha256": hashlib.sha256(ciphertext).hexdigest(),
        "size": len(ciphertext),
        "plaintext_size": plaintext_size,
        "chunks": chunks,
    }


def _decrypt_index(manifest: dict, blobs: dict, master_key: bytes) -> dict:
    name = manifest["index_member"]
    metadata = manifest["members"][name]
    member_key = backup_crypto.derive_member_key(master_key, name)
    out = io.BytesIO()
    backup_crypto.decrypt_member(
        name,
        member_key,
        io.BytesIO(blobs[name]),
        out,
        plaintext_size=metadata["plaintext_size"],
        chunk_count=metadata["chunks"],
    )
    return json.loads(out.getvalue())


def _expect(exc_type, message: str, func) -> None:
    with pytest.raises(exc_type) as excinfo:
        func()
    assert str(excinfo.value) == message


# 1. verify has a keyword-only passphrase parameter.
def test_verify_signature_includes_keyword_only_passphrase():
    parameters = [
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(verify_backup).parameters.items()
    ]
    assert parameters == [
        ("path", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("passphrase", inspect.Parameter.KEYWORD_ONLY, None),
    ]


# 2. v1 without passphrase is unchanged.
def test_v1_verify_without_passphrase_unchanged(tmp_path, make_wav):
    store = _seed(tmp_path, make_wav)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup)
    result = verify_backup(backup)
    assert result["status"] == "PASS"
    assert result["version"] == 1
    assert result["records"] == 2


# 3. v1 with a passphrase still verifies; the passphrase is ignored.
def test_v1_verify_ignores_passphrase(tmp_path, make_wav):
    store = _seed(tmp_path, make_wav)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup)
    result = verify_backup(backup, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert result["version"] == 1


# 4. v2 without a passphrase is rejected with the exact contract error.
def test_v2_without_passphrase_requires_one(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    _expect(ValueError, REQUIRED_ERROR, lambda: verify_backup(backup))


# 5. A wrong passphrase fails manifest authentication exactly.
def test_v2_wrong_passphrase_fails_manifest_authentication(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    _expect(
        ValueError, MANIFEST_ERROR, lambda: verify_backup(backup, passphrase=WRONG)
    )


# 6. The correct passphrase authenticates everything and passes.
def test_v2_correct_passphrase_passes(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    result = verify_backup(backup, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert result["version"] == 2
    assert result["records"] == 2
    assert result["members"] == 3  # transcripts + 2 audio, index excluded
    assert result["expanded_bytes"] > 0
    assert result["path"] == str(backup.resolve())


# 7. The result carries no secrets, index or mapping.
def test_v2_result_contains_no_secret_material(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    result = verify_backup(backup, passphrase=PASSPHRASE)
    assert set(result) == {
        "status",
        "path",
        "version",
        "records",
        "members",
        "expanded_bytes",
        "manifest",
    }
    serialized = json.dumps(result)
    assert PASSPHRASE not in serialized
    assert "transcripts" not in serialized
    assert "sources/" not in serialized
    with zipfile.ZipFile(backup) as archive:
        salt = base64.b64decode(
            json.loads(archive.read("manifest.json"))["encryption"]["salt_base64"]
        )
    master_key = backup_crypto.derive_master_key(PASSPHRASE, salt)
    assert master_key.hex() not in serialized


# 8. A manifest value tamper without re-tagging fails HMAC authentication.
def test_manifest_tamper_fails_authentication(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        # Structurally valid value tamper: only the HMAC layer can catch it.
        manifest["members"]["payload/00000001.enc"]["sha256"] = "1" * 64

    tampered = _tampered_archive(tmp_path, backup, manifest_mutate=mutate)
    _expect(
        ValueError,
        MANIFEST_ERROR,
        lambda: verify_backup(tampered, passphrase=PASSPHRASE),
    )


# 9. Hostile or off-profile KDF parameters are rejected before derivation.
@pytest.mark.parametrize(
    "kdf_params",
    [
        {"iterations": 0, "memory_cost_kib": 65536, "lanes": 1},
        {"iterations": 11, "memory_cost_kib": 65536, "lanes": 1},
        {"iterations": 3, "memory_cost_kib": 512, "lanes": 1},
        {"iterations": 3, "memory_cost_kib": 999_999, "lanes": 1},
        {"iterations": 3, "memory_cost_kib": 65536, "lanes": 0},
        {"iterations": 3, "memory_cost_kib": 65536, "lanes": 5},
        {"iterations": 4, "memory_cost_kib": 65536, "lanes": 1},  # in bounds, off profile
        {"iterations": True, "memory_cost_kib": 65536, "lanes": 1},
        {"iterations": 3, "memory_cost_kib": 65536},
    ],
)
def test_hostile_kdf_parameters_rejected(tmp_path, make_wav, kdf_params):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        manifest["encryption"]["kdf_params"] = kdf_params

    tampered = _tampered_archive(tmp_path, backup, manifest_mutate=mutate)
    with pytest.raises(ValueError) as excinfo:
        verify_backup(tampered, passphrase=PASSPHRASE)
    assert str(excinfo.value) != MANIFEST_ERROR


# 10. Invalid base64, salt/tag sizes and types are rejected.
@pytest.mark.parametrize(
    "field,value",
    [
        ("salt_base64", "!!!not-base64!!!"),
        ("salt_base64", base64.b64encode(b"\x01" * 8).decode("ascii")),
        ("salt_base64", base64.b64encode(b"\x01" * 64).decode("ascii")),
        ("salt_base64", 1234),
        ("manifest_tag_base64", "!!!"),
        ("manifest_tag_base64", base64.b64encode(b"\x01" * 16).decode("ascii")),
        ("manifest_tag_base64", None),
    ],
)
def test_invalid_encoding_and_sizes_rejected(tmp_path, make_wav, field, value):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        manifest["encryption"][field] = value

    tampered = _tampered_archive(tmp_path, backup, manifest_mutate=mutate)
    with pytest.raises(ValueError) as excinfo:
        verify_backup(tampered, passphrase=PASSPHRASE)
    assert str(excinfo.value) != MANIFEST_ERROR


# 11/12/13. Missing, added or renamed members break the exact member set.
def test_missing_member_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    tampered = _tampered_archive(
        tmp_path, backup, blob_mutate=lambda m, b: b.pop("payload/00000002.enc")
    )
    with pytest.raises(ValueError, match="member set"):
        verify_backup(tampered, passphrase=PASSPHRASE)


def test_added_member_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        blobs["payload/00000009.enc"] = b"\x00" * 16
        # Compression record for the new name.
        blobs.setdefault("manifest.json", b"")

    tampered = _tampered_archive(tmp_path, backup, blob_mutate=mutate)
    with pytest.raises(ValueError, match="member set"):
        verify_backup(tampered, passphrase=PASSPHRASE)


def test_renamed_member_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        blobs["payload/00000007.enc"] = blobs.pop("payload/00000002.enc")

    tampered = _tampered_archive(tmp_path, backup, blob_mutate=mutate)
    with pytest.raises(ValueError, match="member set"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 14. Duplicate, unsafe or nonconsecutive opaque names are rejected.
def test_duplicate_member_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    target = tmp_path / "dup.voice-backup"
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in blobs.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression[name]
            archive.writestr(info, data)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("payload/00000001.enc", blobs["payload/00000001.enc"])
    with pytest.raises(ValueError, match="duplicate"):
        verify_backup(target, passphrase=PASSPHRASE)


def test_nonconsecutive_opaque_names_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        entry = manifest["members"].pop("payload/00000002.enc")
        manifest["members"]["payload/00000005.enc"] = entry
        blobs["payload/00000005.enc"] = blobs.pop("payload/00000002.enc")

    tampered = _tampered_archive(
        tmp_path, backup, manifest_mutate=mutate, retag_with=PASSPHRASE
    )
    with pytest.raises(ValueError) as excinfo:
        verify_backup(tampered, passphrase=PASSPHRASE)
    assert str(excinfo.value) != MANIFEST_ERROR


def test_unsafe_opaque_name_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        manifest["members"]["../evil.enc"] = manifest["members"].pop(
            "payload/00000002.enc"
        )

    tampered = _tampered_archive(tmp_path, backup, manifest_mutate=mutate)
    with pytest.raises(ValueError, match="invalid|unsafe|member set"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 15. Encrypted payload members must be ZIP_STORED.
def test_compressed_payload_member_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    compression["payload/00000001.enc"] = zipfile.ZIP_DEFLATED
    target = tmp_path / "deflated.voice-backup"
    _store_archive(target, compression, blobs)
    with pytest.raises(ValueError, match="ZIP_STORED|compression"):
        verify_backup(target, passphrase=PASSPHRASE)


def _member_error(name: str) -> str:
    return f"backup member authentication failed: {name}"


# 16. A flipped ciphertext byte fails member authentication.
def test_ciphertext_byte_flip_fails(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        name = "payload/00000001.enc"
        data = bytearray(blobs[name])
        data[0] ^= 0x01
        blobs[name] = bytes(data)

    tampered = _tampered_archive(tmp_path, backup, blob_mutate=mutate)
    _expect(
        ValueError,
        _member_error("payload/00000001.enc"),
        lambda: verify_backup(tampered, passphrase=PASSPHRASE),
    )


# 17. A flipped tag byte fails member authentication.
def test_tag_byte_flip_fails(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        name = "payload/00000001.enc"
        data = bytearray(blobs[name])
        data[-1] ^= 0x01
        blobs[name] = bytes(data)

    tampered = _tampered_archive(tmp_path, backup, blob_mutate=mutate)
    _expect(
        ValueError,
        _member_error("payload/00000001.enc"),
        lambda: verify_backup(tampered, passphrase=PASSPHRASE),
    )


# 18. Truncated ciphertext fails member authentication.
def test_truncated_ciphertext_fails(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        blobs["payload/00000001.enc"] = blobs["payload/00000001.enc"][:-1]

    tampered = _tampered_archive(tmp_path, backup, blob_mutate=mutate)
    # Truncation is caught either structurally (ZIP size vs manifest) or by
    # the AEAD stream check; both are hard errors with no plaintext fallback.
    with pytest.raises(ValueError, match="does not match the archive|authentication failed"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 19. A wrong ciphertext SHA-256 in an authenticated manifest is a hard error.
def test_wrong_ciphertext_hash_fails(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        manifest["members"]["payload/00000001.enc"]["sha256"] = "0" * 64

    tampered = _tampered_archive(
        tmp_path, backup, manifest_mutate=mutate, retag_with=PASSPHRASE
    )
    with pytest.raises(ValueError, match="integrity"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 20. Wrong size/plaintext_size/chunks metadata is rejected.
@pytest.mark.parametrize("field", ["size", "plaintext_size", "chunks"])
def test_wrong_member_metadata_rejected(tmp_path, make_wav, field):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        manifest["members"]["payload/00000001.enc"][field] += 16

    tampered = _tampered_archive(
        tmp_path, backup, manifest_mutate=mutate, retag_with=PASSPHRASE
    )
    with pytest.raises(ValueError) as excinfo:
        verify_backup(tampered, passphrase=PASSPHRASE)
    assert str(excinfo.value) != MANIFEST_ERROR


# 21. An oversized private index is rejected before decryption.
def test_oversized_private_index_rejected_before_decrypt(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        plaintext_size = 17 * 1024**2
        chunks = -(-plaintext_size // CHUNK_SIZE)
        manifest["members"]["payload/00000000.enc"] = {
            "sha256": "0" * 64,
            "size": plaintext_size + 16 * chunks,
            "plaintext_size": plaintext_size,
            "chunks": chunks,
        }

    tampered = _tampered_archive(
        tmp_path, backup, manifest_mutate=mutate, retag_with=PASSPHRASE
    )
    with pytest.raises(ValueError, match="index|does not match the archive"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 22. Invalid private index JSON after valid AEAD is rejected.
def test_invalid_private_index_json_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    manifest = json.loads(blobs["manifest.json"])
    master_key, manifest_key = _keys(manifest, PASSPHRASE)
    _rewrite_index(manifest, blobs, master_key, b"this is not json")
    tampered = tmp_path / "badindex.voice-backup"
    blobs["manifest.json"] = (
        json.dumps(_retag(manifest, manifest_key), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _store_archive(tampered, compression, blobs)
    with pytest.raises(ValueError, match="index"):
        verify_backup(tampered, passphrase=PASSPHRASE)


def _tampered_index_archive(tmp_path, make_wav, index_mutate) -> Path:
    backup = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    manifest = json.loads(blobs["manifest.json"])
    master_key, manifest_key = _keys(manifest, PASSPHRASE)
    index = _decrypt_index(manifest, blobs, master_key)
    index_mutate(index)
    _rewrite_index(manifest, blobs, master_key, index)
    blobs["manifest.json"] = (
        json.dumps(_retag(manifest, manifest_key), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    target = tmp_path / "indextampered.voice-backup"
    _store_archive(target, compression, blobs)
    return target


# 23. A non-bijective mapping is rejected.
def test_non_bijective_mapping_rejected(tmp_path, make_wav):
    def mutate(index):
        members = index["members"]
        members["config/settings.json"] = members["transcripts.jsonl"]

    tampered = _tampered_index_archive(tmp_path, make_wav, mutate)
    with pytest.raises(ValueError, match="index"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 24. The index may not map itself as a payload.
def test_self_referencing_index_rejected(tmp_path, make_wav):
    def mutate(index):
        index["members"]["payload/00000000.enc"] = "payload/00000000.enc"

    tampered = _tampered_index_archive(tmp_path, make_wav, mutate)
    with pytest.raises(ValueError, match="index"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 25. transcripts.jsonl mapping is mandatory.
def test_missing_transcripts_mapping_rejected(tmp_path, make_wav):
    def mutate(index):
        mapping = index["members"]
        mapping["sources/extra.wav"] = mapping.pop("transcripts.jsonl")

    tampered = _tampered_index_archive(tmp_path, make_wav, mutate)
    with pytest.raises(ValueError, match="transcripts"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 26. Unsafe or unsupported logical members are rejected.
@pytest.mark.parametrize(
    "logical", ["../evil.txt", "etc/passwd", "config/dictionary.json/extra"]
)
def test_unsafe_logical_member_rejected(tmp_path, make_wav, logical):
    def mutate(index):
        mapping = index["members"]
        first_audio = next(name for name in mapping if name.startswith("sources/"))
        mapping[logical] = mapping.pop(first_audio)

    tampered = _tampered_index_archive(tmp_path, make_wav, mutate)
    with pytest.raises(ValueError, match="index"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 27. Fixed logical plaintext limits apply to authenticated metadata.
def test_fixed_logical_limit_overflow_rejected(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def manifest_mutate(manifest, blobs):
        plaintext_size = 600 * 1024**2
        chunks = -(-plaintext_size // CHUNK_SIZE)
        entry = manifest["members"]["payload/00000001.enc"]
        entry["plaintext_size"] = plaintext_size
        entry["chunks"] = chunks
        entry["size"] = plaintext_size + 16 * chunks

    tampered = _tampered_archive(
        tmp_path, backup, manifest_mutate=manifest_mutate, retag_with=PASSPHRASE
    )
    with pytest.raises(ValueError):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 28. config/dictionary.json without config/settings.json is rejected.
def test_dictionary_without_settings_rejected(tmp_path, make_wav):
    store = _seed(tmp_path, make_wav)
    from voice_studio.config import save_settings
    from voice_studio.models import Settings

    dictionary = tmp_path / "dictionary.json"
    dictionary.write_text('{"replacements":[]}', encoding="utf-8")
    settings_file = tmp_path / "config" / "settings.json"
    save_settings(Settings(dictionary_path=str(dictionary)), settings_file)
    backup = tmp_path / "enc.voice-backup"
    create_backup(store, backup, settings_file=settings_file, passphrase=PASSPHRASE)

    compression, blobs = _load_archive(backup)
    manifest = json.loads(blobs["manifest.json"])
    master_key, manifest_key = _keys(manifest, PASSPHRASE)
    index = _decrypt_index(manifest, blobs, master_key)
    # Re-map: drop settings; dictionary takes over one audio opaque name so the
    # value set still covers every non-index payload member.
    mapping = index["members"]
    audio_names = [name for name in mapping if name.startswith("sources/")]
    mapping.pop("config/settings.json")
    mapping["config/dictionary.json"] = mapping.pop(audio_names[0])
    mapping.pop(audio_names[1])
    _rewrite_index(manifest, blobs, master_key, index)
    blobs["manifest.json"] = (
        json.dumps(_retag(manifest, manifest_key), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    target = tmp_path / "dictonly.voice-backup"
    _store_archive(target, compression, blobs)
    with pytest.raises(ValueError, match="index"):
        verify_backup(target, passphrase=PASSPHRASE)


# 29. sources mappings are forbidden when include_audio is False.
def test_sources_mapping_with_audio_disabled_rejected(tmp_path, make_wav):
    def mutate(index):
        index["include_audio"] = False

    tampered = _tampered_index_archive(tmp_path, make_wav, mutate)
    with pytest.raises(ValueError, match="index"):
        verify_backup(tampered, passphrase=PASSPHRASE)


# 30. Verification streams through a discard sink with bounded reads.
def test_verification_streams_with_discard_sink(tmp_path, make_wav, monkeypatch):
    # Force a multi-chunk transcripts payload.
    big = LocalStore(tmp_path / "store")
    for index in range(40):
        transcript = _transcript(f"rec-{index}", "0" * 64, "")
        transcript.raw_text = "x" * 30_000
        big.save(transcript)
    backup = tmp_path / "enc.voice-backup"
    create_backup(big, backup, passphrase=PASSPHRASE)

    seen = {"dest_types": [], "writes": [], "reads": []}
    real_decrypt = backup_crypto.decrypt_member

    class _Probe:
        def __init__(self, stream, record):
            self._stream = stream
            self._record = record

        def read(self, size=-1):
            assert 0 < size <= CHUNK_SIZE + 16
            self._record.append(size)
            return self._stream.read(size)

        def write(self, data):
            self._record.append(len(data))
            return self._stream.write(data)

    def spy(name, key, source, dest, **kwargs):
        seen["dest_types"].append(type(dest).__name__)
        return real_decrypt(
            name, key, _Probe(source, seen["reads"]), _Probe(dest, seen["writes"]),
            **kwargs,
        )

    monkeypatch.setattr(backup_crypto, "decrypt_member", spy)
    result = verify_backup(backup, passphrase=PASSPHRASE)
    assert result["status"] == "PASS"
    assert seen["dest_types"], "no member was decrypted"
    # Only the bounded private index may decrypt into memory; every payload
    # member streams into the discard sink.
    assert seen["dest_types"].count("BytesIO") == 1
    assert seen["dest_types"].count("_DiscardWriter") == result["members"]
    assert all(size <= CHUNK_SIZE for size in seen["writes"])


# 31. Authentication failure never falls back to the v1 parser.
def test_authentication_failure_never_uses_v1_path(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    def mutate(manifest, blobs):
        data = bytearray(blobs["payload/00000001.enc"])
        data[0] ^= 0x01
        blobs["payload/00000001.enc"] = bytes(data)

    tampered = _tampered_archive(tmp_path, backup, blob_mutate=mutate)
    # v1 dispatch would raise "unsupported backup member: payload/...";
    # v2 must raise the exact member authentication error instead.
    _expect(
        ValueError,
        _member_error("payload/00000001.enc"),
        lambda: verify_backup(tampered, passphrase=PASSPHRASE),
    )
    # Wrong passphrase raises the manifest error, never a v1 complaint.
    _expect(
        ValueError, MANIFEST_ERROR, lambda: verify_backup(tampered, passphrase=WRONG)
    )


def test_boolean_version_is_not_accepted_as_legacy_v1(tmp_path, make_wav):
    store = _seed(tmp_path, make_wav)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup)
    compression, blobs = _load_archive(backup)
    manifest = json.loads(blobs["manifest.json"])
    manifest["version"] = True
    blobs["manifest.json"] = json.dumps(manifest).encode("utf-8")
    tampered = tmp_path / "boolean-version.voice-backup"
    _store_archive(tampered, compression, blobs)

    with pytest.raises(ValueError, match="unsupported backup version"):
        verify_backup(tampered)


def test_manifest_must_be_a_json_object(tmp_path, make_wav):
    store = _seed(tmp_path, make_wav)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup)
    compression, blobs = _load_archive(backup)
    blobs["manifest.json"] = b"[]"
    malformed = tmp_path / "list-manifest.voice-backup"
    _store_archive(malformed, compression, blobs)

    with pytest.raises(ValueError, match="manifest.*object"):
        verify_backup(malformed)


def test_invalid_utf8_manifest_has_a_concrete_error(tmp_path, make_wav):
    store = _seed(tmp_path, make_wav)
    backup = tmp_path / "plain.voice-backup"
    create_backup(store, backup)
    compression, blobs = _load_archive(backup)
    blobs["manifest.json"] = b"\xff"
    malformed = tmp_path / "invalid-manifest-utf8.voice-backup"
    _store_archive(malformed, compression, blobs)

    with pytest.raises(ValueError, match="invalid backup manifest"):
        verify_backup(malformed)


def test_empty_v2_passphrase_is_an_authentication_failure(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)

    _expect(ValueError, MANIFEST_ERROR, lambda: verify_backup(backup, passphrase=""))


@pytest.mark.parametrize("logical", ["sources/CON.wav", "sources/name. "])
def test_private_index_rejects_nonportable_logical_names(
    tmp_path, make_wav, logical
):
    def mutate(index):
        mapping = index["members"]
        first_audio = next(name for name in mapping if name.startswith("sources/"))
        mapping[logical] = mapping.pop(first_audio)

    tampered = _tampered_index_archive(tmp_path, make_wav, mutate)

    with pytest.raises(ValueError, match="index|unsafe"):
        verify_backup(tampered, passphrase=PASSPHRASE)


def test_private_index_rejects_portable_logical_name_aliases(tmp_path, make_wav):
    def mutate(index):
        mapping = index["members"]
        audio = [name for name in mapping if name.startswith("sources/")]
        first = mapping.pop(audio[0])
        second = mapping.pop(audio[1])
        mapping["sources/Case.wav"] = first
        mapping["sources/case.wav"] = second

    tampered = _tampered_index_archive(tmp_path, make_wav, mutate)

    with pytest.raises(ValueError, match="index|alias|unsafe"):
        verify_backup(tampered, passphrase=PASSPHRASE)


def test_invalid_utf8_private_index_has_a_concrete_error(tmp_path, make_wav):
    backup = _make_v2(tmp_path, make_wav)
    compression, blobs = _load_archive(backup)
    manifest = json.loads(blobs["manifest.json"])
    master_key, manifest_key = _keys(manifest, PASSPHRASE)
    _rewrite_index(manifest, blobs, master_key, b"\xff")
    blobs["manifest.json"] = (
        json.dumps(_retag(manifest, manifest_key), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    malformed = tmp_path / "invalid-index-utf8.voice-backup"
    _store_archive(malformed, compression, blobs)

    with pytest.raises(ValueError, match="private index"):
        verify_backup(malformed, passphrase=PASSPHRASE)
