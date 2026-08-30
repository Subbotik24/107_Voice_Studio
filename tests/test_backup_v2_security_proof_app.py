"""Automated security/package proofs for encrypted backup v2 (W2-E1 Slice D2a).

Covers design-doc items 17-20: secret hygiene across the full
create/verify/restore/recovery cycle, a frozen-style source probe of the
cryptography primitives, deterministic SBOM generation, and the
deterministic v2 structure contract. These are source-level proofs; the
packaged executable PASS stays in the R0.10 physical acceptance scope.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from voice_studio import backup as backup_module
from voice_studio import backup_crypto
from voice_studio.backup import (
    create_backup,
    recover_interrupted_restore,
    restore_backup,
    verify_backup,
)
from voice_studio.config import load_settings, save_settings
from voice_studio.diagnostics import diagnostics, export_redacted_diagnostics
from voice_studio.models import Settings, Transcript
from voice_studio.storage import LocalStore

PASSPHRASE = "synthetic-d2a-proof-passphrase-9f8e7d6c"
SETTINGS_MARKER = "synthetic-d2a-settings-marker-51c2"
DICTIONARY_MARKER = b"synthetic-d2a-dictionary-marker-7b41"

REPO_ROOT = Path(__file__).resolve().parents[1]
CHUNK = backup_crypto.CHUNK_SIZE


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


def _seed(tmp_path: Path, make_wav):
    """Store + settings with unique markers; returns (store, settings_file)."""
    store = LocalStore(tmp_path / "store")
    original = make_wav(tmp_path / "original.wav")
    managed, digest = store.import_source(original)
    store.save(_transcript("rec-d2a-0", digest, str(managed)))
    dictionary = tmp_path / f"dictionary-{SETTINGS_MARKER}.json"
    dictionary.write_bytes(
        b'{"replacements":[["' + DICTIONARY_MARKER + b'","x"]]}'
    )
    settings_file = tmp_path / "config" / "settings.json"
    # The marker rides inside the settings JSON via the dictionary path.
    save_settings(Settings(dictionary_path=str(dictionary)), settings_file)
    return store, settings_file


def _sidecar_bytes(data: Path) -> bytes:
    sidecar = data / ".restore-settings-v2"
    blob = b""
    for path in sorted(sidecar.rglob("*")):
        if path.is_file():
            blob += path.read_bytes()
    return blob


# --- 1. Secret hygiene across the full cycle --------------------------------


def test_secret_hygiene_full_v2_cycle(tmp_path, make_wav, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    store, settings_file = _seed(tmp_path, make_wav)
    backup = tmp_path / "enc.voice-backup"
    create_result = create_backup(
        store, backup, settings_file=settings_file, passphrase=PASSPHRASE
    )
    verify_result = verify_backup(backup, passphrase=PASSPHRASE)
    assert verify_result["status"] == "PASS"

    # Recover the exact key material for leakage scanning.
    with zipfile.ZipFile(backup) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    import base64

    salt = base64.b64decode(manifest["encryption"]["salt_base64"])
    master_key = backup_crypto.derive_master_key(PASSPHRASE, salt)
    manifest_key = backup_crypto.derive_manifest_key(master_key)

    # Crash the restore after swap_completed so journal + sidecar exist.
    settings_target = tmp_path / "live" / "settings.json"
    settings_target.parent.mkdir(parents=True)
    settings_target.write_text(json.dumps(Settings().to_dict()), encoding="utf-8")

    def _die(*args, **kwargs):
        raise KeyboardInterrupt

    original_apply = backup_module._apply_restored_settings
    backup_module._apply_restored_settings = _die
    try:
        with pytest.raises(KeyboardInterrupt):
            restore_backup(
                backup, data, settings_target=settings_target, passphrase=PASSPHRASE
            )
    finally:
        backup_module._apply_restored_settings = original_apply

    journal_bytes = backup_module.restore_journal_path(data).read_bytes()
    sidecar_bytes = _sidecar_bytes(data)
    diagnostics_file = tmp_path / "diagnostics.json"
    export_redacted_diagnostics(diagnostics(load_settings(settings_file)), diagnostics_file)
    diagnostics_bytes = diagnostics_file.read_bytes()
    results_bytes = json.dumps([create_result, verify_result]).encode("utf-8")

    forbidden = [
        PASSPHRASE.encode(),
        master_key,
        manifest_key,
        *(
            backup_crypto.derive_member_key(master_key, member_name)
            for member_name in manifest["members"]
        ),
    ]
    for label, blob in (
        ("source settings", settings_file.read_bytes()),
        ("pending live settings", settings_target.read_bytes()),
        ("restore journal", journal_bytes),
        ("encrypted sidecar", sidecar_bytes),
        ("exported diagnostics", diagnostics_bytes),
        ("API JSON results", results_bytes),
    ):
        for secret in forbidden:
            assert secret not in blob, f"secret material leaked into {label}"

    # Plaintext settings/dictionary markers never appear in journal/sidecar.
    for label, blob in (
        ("restore journal", journal_bytes),
        ("encrypted sidecar", sidecar_bytes),
    ):
        assert SETTINGS_MARKER.encode() not in blob, f"settings marker in {label}"
        assert DICTIONARY_MARKER not in blob, f"dictionary marker in {label}"

    # Completing recovery writes settings/dictionary only to the user-visible
    # targets, and the sidecar/journal disappear.
    recovered = recover_interrupted_restore(
        data, settings_target=settings_target, passphrase=PASSPHRASE
    )
    assert recovered["status"] == "PASS"
    assert not (data / ".restore-settings-v2").exists()
    assert not backup_module.restore_journal_path(data).exists()
    restored = json.loads(settings_target.read_text(encoding="utf-8"))
    # The restore contract rewrites dictionary_path to the restored copy.
    assert restored["dictionary_path"] == str(
        settings_target.parent / "dictionary.restored.json"
    )
    dictionary_bytes = (settings_target.parent / "dictionary.restored.json").read_bytes()
    assert DICTIONARY_MARKER in dictionary_bytes
    for label, blob in (
        ("restored settings", settings_target.read_bytes()),
        ("restored dictionary", dictionary_bytes),
        ("recovery API JSON result", json.dumps(recovered).encode("utf-8")),
    ):
        for secret in forbidden:
            assert secret not in blob, f"secret material leaked into {label}"


def test_secret_hygiene_git_tracked_files_contain_no_cycle_secrets(tmp_path, make_wav):
    """The cycle passphrase/key bytes exist only in this test file."""
    listing = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    offenders = []
    for relative in listing:
        content = (REPO_ROOT / relative).read_bytes()
        if PASSPHRASE.encode() in content:
            offenders.append(relative)
    # The synthetic value is intentionally declared in this regression test;
    # production, documentation and packaging files must never contain it.
    assert all(offender.startswith("tests/") for offender in offenders)


def test_cli_cycle_never_prints_passphrase(tmp_path, make_wav, monkeypatch, capsys):
    from voice_studio.cli import main

    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    _seed(tmp_path, make_wav)

    class _Tty:
        @staticmethod
        def isatty() -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", _Tty())
    answers = [PASSPHRASE, PASSPHRASE, PASSPHRASE, PASSPHRASE]
    monkeypatch.setattr("getpass.getpass", lambda prompt="": answers.pop(0))

    out = tmp_path / "cli-enc.voice-backup"
    assert main(["backup", "create", str(out), "--encrypt"]) == 0
    assert main(["backup", "verify", str(out)]) == 0
    assert main(["backup", "restore", str(out)]) == 0
    captured = capsys.readouterr()
    assert PASSPHRASE not in captured.out
    assert PASSPHRASE not in captured.err


# --- 2. Frozen-style source probe of the cryptography boundary ---------------


def test_frozen_style_crypto_primitives_probe():
    """Source/frozen-style proof: the pinned cryptography stack round-trips.

    Proves importability and a full primitive round-trip of the exact
    building blocks the PyInstaller spec collects; it is NOT a packaged
    executable PASS (that stays in the R0.10 physical acceptance scope).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.hmac import HMAC
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    assert Argon2id is not None and HKDF is not None and HMAC is not None
    assert SHA256 is not None and SHA256.digest_size == 32

    salt = bytes(range(backup_crypto.SALT_SIZE))
    master_key = backup_crypto.derive_master_key(PASSPHRASE, salt)
    manifest_key = backup_crypto.derive_manifest_key(master_key)
    member_key = backup_crypto.derive_member_key(master_key, "payload/00000000.enc")
    assert len({master_key, manifest_key, member_key}) == 3

    # HKDF/HMAC round-trip through the manifest tag path.
    canonical = b'{"probe":true}'
    tag = backup_crypto.compute_manifest_tag(manifest_key, canonical)
    backup_crypto.verify_manifest_tag(manifest_key, canonical, tag)

    # AES-GCM chunked member round-trip through the streaming API.
    plaintext = b"frozen-style-probe" * 100_000  # ~1.8 MB, multi-chunk
    buffer = io.BytesIO()
    plaintext_size, chunk_count = backup_crypto.encrypt_member(
        "payload/00000000.enc", member_key, io.BytesIO(plaintext), buffer
    )
    assert plaintext_size == len(plaintext)
    assert chunk_count == -(-len(plaintext) // CHUNK)
    out = io.BytesIO()
    backup_crypto.decrypt_member(
        "payload/00000000.enc",
        member_key,
        io.BytesIO(buffer.getvalue()),
        out,
        plaintext_size=plaintext_size,
        chunk_count=chunk_count,
    )
    assert out.getvalue() == plaintext
    # A direct AESGCM round-trip pins the primitive itself.
    nonce = b"\x00" * 12
    aead = AESGCM(member_key)
    assert aead.decrypt(nonce, aead.encrypt(nonce, b"probe", None), None) == b"probe"


# --- 3. Deterministic SBOM ---------------------------------------------------


def test_sbom_is_byte_identical_across_directories(tmp_path):
    lock = REPO_ROOT / "requirements-windows.lock"
    outputs = []
    for name in ("dir-a", "dir-b"):
        target_dir = tmp_path / name
        target_dir.mkdir()
        output = target_dir / "sbom.json"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "generate_sbom.py"),
                "--lock",
                str(lock),
                "--project-name",
                "voice-studio",
                "--project-version",
                "0.3.0rc1",
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs.append(output.read_bytes())

    first, second = outputs
    assert first == second, "SBOM must be byte-identical across output directories"
    document = json.loads(first)
    components = document["components"]
    assert len(components) == 59
    assert any(
        item["name"] == "cryptography" and item["version"] == "50.0.1"
        for item in components
    )
    # No absolute or private path leaks into the artifact.
    for marker in (str(tmp_path).encode(), str(REPO_ROOT).encode()):
        assert marker not in first
    print(
        "SBOM proof: components=59 size="
        f"{len(first)} sha256={hashlib.sha256(first).hexdigest()}"
    )


# --- 4. Deterministic v2 structure contract ----------------------------------


def _structure(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        payloads = {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith("payload/")
        }
    return {"manifest": manifest, "payloads": payloads}


def test_v2_structure_is_deterministic_while_ciphertext_differs(tmp_path, make_wav):
    store, settings_file = _seed(tmp_path, make_wav)
    first = tmp_path / "first.voice-backup"
    second = tmp_path / "second.voice-backup"
    create_backup(store, first, settings_file=settings_file, passphrase=PASSPHRASE)
    create_backup(store, second, settings_file=settings_file, passphrase=PASSPHRASE)

    a = _structure(first)
    b = _structure(second)

    # Same logical schema and member/chunk structure.
    assert set(a["manifest"]) == set(b["manifest"])
    assert set(a["manifest"]["members"]) == set(b["manifest"]["members"])
    for name in a["manifest"]["members"]:
        meta_a = a["manifest"]["members"][name]
        meta_b = b["manifest"]["members"][name]
        assert set(meta_a) == set(meta_b)
        assert meta_a["plaintext_size"] == meta_b["plaintext_size"]
        assert meta_a["chunks"] == meta_b["chunks"]
        assert meta_a["size"] == meta_b["size"]
    # Random salt/nonces: ciphertext and salt differ between runs.
    assert (
        a["manifest"]["encryption"]["salt_base64"]
        != b["manifest"]["encryption"]["salt_base64"]
    )
    assert any(
        a["payloads"][name] != b["payloads"][name] for name in a["payloads"]
    )


@pytest.mark.parametrize(
    "size, expected_chunks",
    [
        (0, 1),
        (1, 1),
        (CHUNK, 1),
        (CHUNK + 1, 2),
    ],
    ids=["0B", "1B", "1MiB", "1MiB+1"],
)
def test_chunk_sizing_matrix(size, expected_chunks):
    key = b"\x01" * backup_crypto.KEY_SIZE
    plaintext = b"\x55" * size
    buffer = io.BytesIO()
    plaintext_size, chunk_count = backup_crypto.encrypt_member(
        "payload/00000000.enc", key, io.BytesIO(plaintext), buffer
    )
    assert (plaintext_size, chunk_count) == (size, expected_chunks)
    # Each chunk is plaintext + 16-byte GCM tag; no custom framing bytes.
    assert len(buffer.getvalue()) == size + 16 * expected_chunks
    out = io.BytesIO()
    backup_crypto.decrypt_member(
        "payload/00000000.enc",
        key,
        io.BytesIO(buffer.getvalue()),
        out,
        plaintext_size=plaintext_size,
        chunk_count=chunk_count,
    )
    assert out.getvalue() == plaintext
