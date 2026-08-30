"""Cryptographic primitives for encrypted backup v2 (W2-E1).

Only reviewed primitives from the ``cryptography`` package are used:
Argon2id (passphrase KDF), HKDF-SHA-256 (key separation), AES-256-GCM
(chunked authenticated encryption) and HMAC-SHA-256 (manifest
authentication). This module contains no custom cryptography.

Contract source:
``docs/superpowers/plans/2026-08-30-w2-e1-encrypted-backup-design.md``
sections 5 (format, key schedule, error contract), 7 (secret-material
prohibitions) and 8 (streaming budgets).

Secret-material rules: passphrases and keys are never logged or persisted.
Derivation helpers return short-lived in-memory ``bytes`` only to their
immediate caller.

Slice A scope: key derivation, chunked member encryption/decryption and
manifest authentication only. No ZIP, backup, CLI or GUI integration.
"""
from __future__ import annotations

import hashlib
from typing import BinaryIO

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.hmac import HMAC
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CHUNK_SIZE = 1024 * 1024  # 1 MiB plaintext per AEAD chunk
KEY_SIZE = 32
SALT_SIZE = 16
GCM_TAG_SIZE = 16

ARGON2_ITERATIONS = 3
ARGON2_LANES = 1
ARGON2_MEMORY_COST_KIB = 65536

_MANIFEST_KEY_INFO = b"voice-studio-backup-v2-manifest-key"
_MEMBER_KEY_INFO_PREFIX = b"voice-studio-backup-v2-member:"
_MANIFEST_TAG_DOMAIN = b"voice-studio-backup-v2-manifest"
_NONCE_PREFIX = b"\x00\x00\x00\x00"

MANIFEST_AUTH_ERROR = (
    "backup authentication failed: wrong passphrase or corrupted manifest"
)


def _member_auth_error(member_name: str) -> str:
    return f"backup member authentication failed: {member_name}"


def derive_master_key(passphrase: str, salt: bytes) -> bytes:
    """Derive the 32-byte master key from a passphrase with Argon2id.

    The passphrase is encoded as exact UTF-8 with no Unicode
    normalization: NFC and NFD spellings deliberately derive different
    keys. ``salt`` must be exactly 16 bytes. There is no fallback KDF:
    if Argon2id is unavailable in this runtime a concrete RuntimeError
    is raised instead of silently degrading.
    """
    if not isinstance(passphrase, str):
        raise TypeError("passphrase must be a str")
    if len(salt) != SALT_SIZE:
        raise ValueError(
            f"backup v2 salt must be exactly {SALT_SIZE} bytes, got {len(salt)}"
        )
    try:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    except ImportError as exc:
        raise RuntimeError(
            "Argon2id KDF is unavailable in this runtime: encrypted backup v2 "
            "requires the 'cryptography' package with Argon2id support "
            "(cryptography>=44). There is no fallback KDF; install a supported "
            "cryptography version instead."
        ) from exc
    kdf = Argon2id(
        salt=salt,
        length=KEY_SIZE,
        iterations=ARGON2_ITERATIONS,
        lanes=ARGON2_LANES,
        memory_cost=ARGON2_MEMORY_COST_KIB,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _hkdf_sha256(master_key: bytes, info: bytes) -> bytes:
    if len(master_key) != KEY_SIZE:
        raise ValueError(f"master key must be {KEY_SIZE} bytes")
    return HKDF(
        algorithm=SHA256(), length=KEY_SIZE, salt=None, info=info
    ).derive(master_key)


def derive_manifest_key(master_key: bytes) -> bytes:
    """Derive the manifest authentication key (HKDF domain separation)."""
    return _hkdf_sha256(master_key, _MANIFEST_KEY_INFO)


def derive_member_key(master_key: bytes, member_name: str) -> bytes:
    """Derive the per-member AEAD key (HKDF domain separation by name)."""
    return _hkdf_sha256(
        master_key, _MEMBER_KEY_INFO_PREFIX + member_name.encode("utf-8")
    )


def _chunk_nonce(chunk_index: int) -> bytes:
    return _NONCE_PREFIX + chunk_index.to_bytes(8, "big")


def _chunk_associated_data(name_bytes: bytes, chunk_index: int, is_final: bool) -> bytes:
    return (
        name_bytes
        + b"\x00"
        + chunk_index.to_bytes(8, "big")
        + (b"\x01" if is_final else b"\x00")
    )


def _read_up_to(source: BinaryIO, size: int) -> bytes:
    """Fill one bounded block while allowing ordinary short stream reads."""
    parts: list[bytes] = []
    remaining = size
    while remaining:
        block = source.read(remaining)
        if not block:
            break
        parts.append(block)
        remaining -= len(block)
    return b"".join(parts)


def encrypt_member(
    member_name: str,
    member_key: bytes,
    source: BinaryIO,
    dest: BinaryIO,
) -> tuple[int, int]:
    """Encrypt ``source`` into ``dest`` as chunked AES-256-GCM.

    Streams in 1 MiB plaintext chunks with at most one chunk of bounded
    lookahead; ``source`` is only ever read with an explicit size, so the
    working set stays independent of member size. Returns
    ``(plaintext_size, chunk_count)`` for the authenticated manifest.
    Empty plaintext is represented by one final empty chunk (16 bytes of
    ciphertext). No custom framing bytes are written.
    """
    aead = AESGCM(member_key)
    name_bytes = member_name.encode("utf-8")
    plaintext_size = 0
    chunk_index = 0
    current = _read_up_to(source, CHUNK_SIZE)
    while True:
        following = _read_up_to(source, CHUNK_SIZE)
        is_final = not following
        ciphertext = aead.encrypt(
            _chunk_nonce(chunk_index),
            current,
            _chunk_associated_data(name_bytes, chunk_index, is_final),
        )
        dest.write(ciphertext)
        plaintext_size += len(current)
        chunk_index += 1
        if is_final:
            return plaintext_size, chunk_index
        current = following


def decrypt_member(
    member_name: str,
    member_key: bytes,
    source: BinaryIO,
    dest: BinaryIO,
    *,
    plaintext_size: int,
    chunk_count: int,
) -> None:
    """Decrypt chunked AES-256-GCM ciphertext from ``source`` into ``dest``.

    ``plaintext_size`` and ``chunk_count`` come from the authenticated
    manifest and are validated against the stream structure before and
    during decryption. Any wrong key, tampered/reordered/truncated chunk
    or size/count mismatch raises exactly
    ``ValueError("backup member authentication failed: <member_name>")``;
    this function never reports success for unauthenticated data.

    ``dest`` must be a discardable staging sink: on failure it may hold
    partial plaintext from earlier authenticated chunks and the caller
    must delete it (the restore journal cleans staging; callers must not
    treat partial output as usable data).
    """
    error = _member_auth_error(member_name)
    if (
        chunk_count < 1
        or plaintext_size < 0
        or plaintext_size > (chunk_count * CHUNK_SIZE)
        or plaintext_size < (chunk_count - 1) * CHUNK_SIZE
    ):
        raise ValueError(error)
    final_plaintext_size = plaintext_size - (chunk_count - 1) * CHUNK_SIZE
    aead = AESGCM(member_key)
    name_bytes = member_name.encode("utf-8")
    for chunk_index in range(chunk_count):
        is_final = chunk_index == chunk_count - 1
        expected_plaintext = final_plaintext_size if is_final else CHUNK_SIZE
        expected_ciphertext = expected_plaintext + GCM_TAG_SIZE
        ciphertext = _read_up_to(source, expected_ciphertext)
        if len(ciphertext) != expected_ciphertext:
            raise ValueError(error)
        try:
            plaintext = aead.decrypt(
                _chunk_nonce(chunk_index),
                ciphertext,
                _chunk_associated_data(name_bytes, chunk_index, is_final),
            )
        except InvalidTag:
            raise ValueError(error) from None
        dest.write(plaintext)
    if source.read(1):
        raise ValueError(error)


def _manifest_tag_message(canonical_manifest_bytes: bytes) -> bytes:
    return _MANIFEST_TAG_DOMAIN + hashlib.sha256(canonical_manifest_bytes).digest()


def compute_manifest_tag(manifest_key: bytes, canonical_manifest_bytes: bytes) -> bytes:
    """Compute the 32-byte HMAC-SHA-256 manifest authentication tag."""
    hmac_ctx = HMAC(manifest_key, SHA256())
    hmac_ctx.update(_manifest_tag_message(canonical_manifest_bytes))
    return hmac_ctx.finalize()


def verify_manifest_tag(
    manifest_key: bytes, canonical_manifest_bytes: bytes, tag: bytes
) -> None:
    """Verify the manifest tag with a constant-time comparison.

    Any mismatch raises exactly
    ``ValueError("backup authentication failed: wrong passphrase or
    corrupted manifest")`` with no distinction between a wrong
    passphrase and corruption.
    """
    hmac_ctx = HMAC(manifest_key, SHA256())
    hmac_ctx.update(_manifest_tag_message(canonical_manifest_bytes))
    try:
        hmac_ctx.verify(tag)
    except InvalidSignature:
        raise ValueError(MANIFEST_AUTH_ERROR) from None
