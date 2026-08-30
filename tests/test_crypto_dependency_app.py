"""W2-E1 Slice 0 — cryptography dependency/provenance boundary.

Pins the exact reviewed dependency, the frozen-collection strategy and the
runtime primitive surface needed by the encrypted backup design. No backup
code or API is involved in this slice.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_the_reviewed_cryptography_dependency() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    dependencies_section = pyproject.split("[project.optional-dependencies]", 1)[0]
    assert '"cryptography>=50,<51",' in dependencies_section


def test_windows_lock_pins_the_exact_cryptography_release() -> None:
    rows = (PROJECT_ROOT / "requirements-windows.lock").read_text(encoding="utf-8")

    assert "\ncryptography==50.0.1\n" in f"\n{rows}"


def test_frozen_bundle_collects_cryptography_submodules() -> None:
    spec = (PROJECT_ROOT / "packaging" / "voice_studio.spec").read_text(encoding="utf-8")

    assert 'collect_submodules("cryptography")' in spec


def test_source_runtime_exposes_the_required_primitives() -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.hmac import HMAC
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    key = Argon2id(
        salt=b"\x01" * 16,
        length=32,
        iterations=1,
        lanes=1,
        memory_cost=1024,
    ).derive(b"test-passphrase")
    assert len(key) == 32

    derived = HKDF(algorithm=SHA256(), length=32, salt=None, info=b"slice-0").derive(key)
    assert derived != key

    hmac_instance = HMAC(key, SHA256())
    hmac_instance.update(b"manifest")
    tag = hmac_instance.finalize()
    verifier = HMAC(key, SHA256())
    verifier.update(b"manifest")
    verifier.verify(tag)

    aesgcm = AESGCM(key)
    nonce = b"\x00" * 12
    ciphertext = aesgcm.encrypt(nonce, b"chunk", b"ad")
    assert aesgcm.decrypt(nonce, ciphertext, b"ad") == b"chunk"
