"""Tests for voice_studio.backup_crypto (W2-E1 Slice A).

Contract source:
docs/superpowers/plans/2026-08-30-w2-e1-encrypted-backup-design.md
sections 5 (format/key schedule/error contract) and 7 (secret-material rules).
All passphrases and keys in this file are synthetic test material.
"""
from __future__ import annotations

import io
import sys

import pytest

from voice_studio import backup_crypto as bc

PASSPHRASE = "synthetic test passphrase"
SALT_A = b"\x01" * 16
SALT_B = b"\x02" * 16
MEMBER = "payload/00000001.enc"
MEMBER_OTHER = "payload/00000002.enc"

CHUNK = bc.CHUNK_SIZE
TAG = 16


def _master_key(salt: bytes = SALT_A, passphrase: str = PASSPHRASE) -> bytes:
    return bc.derive_master_key(passphrase, salt)


def _member_key(name: str = MEMBER, salt: bytes = SALT_A) -> bytes:
    return bc.derive_member_key(_master_key(salt), name)


def _encrypt(name: str, key: bytes, plaintext: bytes) -> tuple[bytes, int, int]:
    dest = io.BytesIO()
    size, chunks = bc.encrypt_member(name, key, io.BytesIO(plaintext), dest)
    return dest.getvalue(), size, chunks


def _decrypt(name: str, key: bytes, ciphertext: bytes, size: int, chunks: int) -> bytes:
    dest = io.BytesIO()
    bc.decrypt_member(
        name, key, io.BytesIO(ciphertext), dest,
        plaintext_size=size, chunk_count=chunks,
    )
    return dest.getvalue()


def _roundtrip(name: str, plaintext: bytes) -> tuple[bytes, bytes, int, int]:
    key = _member_key(name)
    ciphertext, size, chunks = _encrypt(name, key, plaintext)
    assert size == len(plaintext)
    assert len(ciphertext) == len(plaintext) + TAG * chunks
    assert _decrypt(name, key, ciphertext, size, chunks) == plaintext
    return key, ciphertext, size, chunks


def _expect_member_error(name: str, func) -> None:
    with pytest.raises(ValueError) as excinfo:
        func()
    assert str(excinfo.value) == f"backup member authentication failed: {name}"


# 1. Argon2id derivation is deterministic for the same passphrase and salt.
def test_master_key_derivation_is_deterministic():
    key1 = _master_key()
    key2 = _master_key()
    assert key1 == key2
    assert len(key1) == 32
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    reference = Argon2id(
        salt=SALT_A, length=32, iterations=3, lanes=1, memory_cost=65536
    ).derive(PASSPHRASE.encode("utf-8"))
    assert key1 == reference


# 2. A different salt produces a different master key.
def test_different_salt_produces_different_key():
    assert _master_key(SALT_A) != _master_key(SALT_B)


# 3. No Unicode normalization: NFC and NFD forms derive different keys.
def test_unicode_normalization_forms_produce_different_keys():
    nfc = "passphrase\u00e9"  # é as a single code point
    nfd = "passphrasee\u0301"  # e + combining acute accent
    assert nfc != nfd
    assert _master_key(SALT_A, nfc) != _master_key(SALT_A, nfd)


# 4. Unavailable Argon2id is a concrete runtime error, never a fallback.
def test_unavailable_argon2id_raises_concrete_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.kdf.argon2", None)
    with pytest.raises(RuntimeError) as excinfo:
        _master_key()
    message = str(excinfo.value).lower()
    assert "argon2id" in message
    assert "fallback" in message  # states explicitly that none exists


# 5. Manifest and member keys are domain-separated.
def test_manifest_and_member_keys_are_domain_separated():
    master = _master_key()
    manifest_key = bc.derive_manifest_key(master)
    assert len(manifest_key) == 32
    for name in ("", MEMBER, "payload/00000000.enc"):
        assert bc.derive_member_key(master, name) != manifest_key


# 6. Different member names derive different member keys.
def test_member_keys_differ_per_member_name():
    master = _master_key()
    assert bc.derive_member_key(master, MEMBER) != bc.derive_member_key(master, MEMBER_OTHER)


# 7. Round-trips: empty, 1 byte, exactly one chunk, chunk+1, multi-chunk.
@pytest.mark.parametrize(
    "size",
    [0, 1, CHUNK, CHUNK + 1, 3 * CHUNK + 123],
    ids=["empty", "one-byte", "one-chunk", "chunk-plus-one", "multi-chunk"],
)
def test_member_roundtrip_streaming(size):
    plaintext = bytes((i * 31 + 7) % 256 for i in range(size))
    _roundtrip(MEMBER, plaintext)


# 8. Zero-length plaintext is one final empty chunk: 16 bytes of ciphertext.
def test_empty_plaintext_is_single_chunk_with_tag_only():
    _, ciphertext, size, chunks = _roundtrip(MEMBER, b"")
    assert size == 0
    assert chunks == 1
    assert ciphertext == bytes(TAG) or len(ciphertext) == TAG
    assert len(ciphertext) == TAG


# 9. Every non-final ciphertext chunk is exactly 1 MiB plaintext + 16 B tag.
def test_non_final_chunks_have_exact_ciphertext_size():
    plaintext = bytes(CHUNK + 1)
    _, ciphertext, size, chunks = _roundtrip(MEMBER, plaintext)
    assert size == CHUNK + 1
    assert chunks == 2
    assert len(ciphertext) == (CHUNK + TAG) + (1 + TAG)


# 10. Decryption with the wrong key fails authentication.
def test_decrypt_with_wrong_key_fails():
    key, ciphertext, size, chunks = _roundtrip(MEMBER, b"secret payload")
    wrong_key = _member_key(MEMBER, salt=SALT_B)
    _expect_member_error(
        MEMBER,
        lambda: _decrypt(MEMBER, wrong_key, ciphertext, size, chunks),
    )
    assert key != wrong_key


def _tampered(ct: bytes, offset: int) -> bytes:
    return ct[:offset] + bytes([ct[offset] ^ 0x01]) + ct[offset + 1 :]


# 11. A flipped ciphertext byte fails authentication.
def test_flipped_ciphertext_byte_fails():
    _, ciphertext, size, chunks = _roundtrip(MEMBER, b"some payload bytes")
    _expect_member_error(
        MEMBER,
        lambda: _decrypt(MEMBER, _member_key(), _tampered(ciphertext, 0), size, chunks),
    )


# 12. A flipped tag byte fails authentication.
def test_flipped_tag_byte_fails():
    _, ciphertext, size, chunks = _roundtrip(MEMBER, b"some payload bytes")
    _expect_member_error(
        MEMBER,
        lambda: _decrypt(MEMBER, _member_key(), _tampered(ciphertext, len(ciphertext) - 1),
                         size, chunks),
    )


# 13. Reordered chunks fail authentication.
def test_reordered_chunks_fail():
    plaintext = bytes(CHUNK) + bytes(reversed(range(256))) * (CHUNK // 256)
    _, ciphertext, size, chunks = _roundtrip(MEMBER, plaintext)
    assert chunks == 2
    first, second = ciphertext[: CHUNK + TAG], ciphertext[CHUNK + TAG :]
    reordered = second + first
    _expect_member_error(
        MEMBER,
        lambda: _decrypt(MEMBER, _member_key(), reordered, size, chunks),
    )


# 14. A truncated final chunk fails authentication.
def test_truncated_final_chunk_fails():
    _, ciphertext, size, chunks = _roundtrip(MEMBER, b"payload to truncate")
    _expect_member_error(
        MEMBER,
        lambda: _decrypt(MEMBER, _member_key(), ciphertext[:-1], size, chunks),
    )


# 15. A wrong authenticated chunk count is a hard error.
def test_wrong_chunk_count_fails():
    key, ciphertext, size, chunks = _roundtrip(MEMBER, bytes(CHUNK + 1))
    for wrong_count in (chunks - 1, chunks + 1):
        _expect_member_error(
            MEMBER,
            lambda wc=wrong_count: _decrypt(MEMBER, key, ciphertext, size, wc),
        )
    _, ct1, size1, _ = _roundtrip(MEMBER, b"short")
    _expect_member_error(
        MEMBER,
        lambda: _decrypt(MEMBER, _member_key(), ct1, size1, 0),
    )


# 16. A wrong authenticated plaintext size is a hard error.
def test_plaintext_size_mismatch_fails():
    key, ciphertext, size, chunks = _roundtrip(MEMBER, b"size must be authenticated")
    for wrong_size in (size - 1, size + 1, size + CHUNK):
        _expect_member_error(
            MEMBER,
            lambda ws=wrong_size: _decrypt(MEMBER, key, ciphertext, ws, chunks),
        )


def _manifest_material():
    master = _master_key()
    return bc.derive_manifest_key(master), b'{"version":2,"members":{}}'


# 17. Manifest HMAC tag computes and verifies.
def test_manifest_tag_roundtrip():
    manifest_key, canonical = _manifest_material()
    tag = bc.compute_manifest_tag(manifest_key, canonical)
    assert len(tag) == 32
    bc.verify_manifest_tag(manifest_key, canonical, tag)


def _expect_manifest_error(func) -> None:
    with pytest.raises(ValueError) as excinfo:
        func()
    assert str(excinfo.value) == (
        "backup authentication failed: wrong passphrase or corrupted manifest"
    )


# 18. Changing one canonical manifest byte breaks verification.
def test_manifest_tag_fails_on_modified_canonical_bytes():
    manifest_key, canonical = _manifest_material()
    tag = bc.compute_manifest_tag(manifest_key, canonical)
    modified = canonical[:-1] + b"X"
    _expect_manifest_error(lambda: bc.verify_manifest_tag(manifest_key, modified, tag))


# 19. A wrong manifest key or wrong tag fails verification.
def test_manifest_tag_fails_with_wrong_key_or_tag():
    manifest_key, canonical = _manifest_material()
    tag = bc.compute_manifest_tag(manifest_key, canonical)
    wrong_key = bc.derive_manifest_key(_master_key(SALT_B))
    _expect_manifest_error(lambda: bc.verify_manifest_tag(wrong_key, canonical, tag))
    bad_tag = _tampered(tag, 3)
    _expect_manifest_error(lambda: bc.verify_manifest_tag(manifest_key, canonical, bad_tag))


# 20. Streaming guard: sources are read only in bounded chunk-sized reads.
class _CountingReader:
    def __init__(self, data: bytes):
        self._buffer = io.BytesIO(data)
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert size != -1, "unbounded read() on backup member source"
        assert size <= CHUNK + TAG, f"read size {size} exceeds one ciphertext chunk"
        self.read_sizes.append(size)
        return self._buffer.read(size)


def test_encrypt_and_decrypt_stream_with_bounded_reads():
    plaintext = bytes(2 * CHUNK + 5)
    key = _member_key()
    source = _CountingReader(plaintext)
    dest = io.BytesIO()
    size, chunks = bc.encrypt_member(MEMBER, key, source, dest)
    assert chunks == 3
    assert source.read_sizes
    assert all(0 < requested <= CHUNK for requested in source.read_sizes)

    ciphertext = dest.getvalue()
    ct_source = _CountingReader(ciphertext)
    out = io.BytesIO()
    bc.decrypt_member(
        MEMBER, key, ct_source, out, plaintext_size=size, chunk_count=chunks
    )
    assert out.getvalue() == plaintext
    assert ct_source.read_sizes == [CHUNK + TAG, CHUNK + TAG, 5 + TAG, 1]


def test_decrypt_rejects_trailing_ciphertext_after_declared_chunks():
    key, ciphertext, size, chunks = _roundtrip(MEMBER, b"authenticated payload")

    _expect_member_error(
        MEMBER,
        lambda: _decrypt(MEMBER, key, ciphertext + b"extra", size, chunks),
    )


class _ShortReader:
    """A valid binary stream that deliberately returns partial reads."""

    def __init__(self, data: bytes, limit: int):
        self._buffer = io.BytesIO(data)
        self._limit = limit

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "unbounded read() on backup member source"
        return self._buffer.read(min(size, self._limit))


def test_encrypt_handles_valid_short_reads_without_creating_short_nonfinal_chunks():
    plaintext = bytes(2 * CHUNK + 5)
    key = _member_key()
    encrypted = io.BytesIO()

    size, chunks = bc.encrypt_member(
        MEMBER,
        key,
        _ShortReader(plaintext, 64 * 1024),
        encrypted,
    )

    assert size == len(plaintext)
    assert chunks == 3
    decrypted = io.BytesIO()
    bc.decrypt_member(
        MEMBER,
        key,
        _ShortReader(encrypted.getvalue(), 64 * 1024),
        decrypted,
        plaintext_size=size,
        chunk_count=chunks,
    )
    assert decrypted.getvalue() == plaintext


class _PlaintextWindowGuard:
    """Reject more than one chunk plus finality lookahead before a write."""

    def __init__(self, data: bytes):
        self._buffer = io.BytesIO(data)
        self.returned_since_write = 0

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "unbounded read() on backup member source"
        block = self._buffer.read(size)
        self.returned_since_write += len(block)
        assert self.returned_since_write <= CHUNK + 1
        return block


class _PlaintextWindowWriter(io.BytesIO):
    def __init__(self, source: _PlaintextWindowGuard):
        super().__init__()
        self._source = source

    def write(self, data: bytes) -> int:
        self._source.returned_since_write = 0
        return super().write(data)


def test_encrypt_limits_plaintext_window_to_one_chunk_plus_lookahead():
    plaintext = bytes(2 * CHUNK + 5)
    source = _PlaintextWindowGuard(plaintext)
    encrypted = _PlaintextWindowWriter(source)

    size, chunks = bc.encrypt_member(MEMBER, _member_key(), source, encrypted)

    assert size == len(plaintext)
    assert chunks == 3
