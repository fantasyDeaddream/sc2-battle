from __future__ import annotations

import hashlib
import hmac
import os
import platform
from typing import Tuple

PASSWORD_ROUNDS = 240000
CRED_ROUNDS = 180000
CRED_MAGIC = b"LADDERC1"


def _pbkdf2_hmac(
    hash_name: str,
    password: bytes,
    salt: bytes,
    rounds: int,
    dklen: int,
) -> bytes:
    builtin = getattr(hashlib, "pbkdf2_hmac", None)
    if callable(builtin):
        return builtin(hash_name, password, salt, rounds, dklen=dklen)

    if rounds <= 0:
        raise ValueError("rounds must be > 0")
    if dklen <= 0:
        raise ValueError("dklen must be > 0")

    digest_size = hashlib.new(hash_name).digest_size
    blocks = (dklen + digest_size - 1) // digest_size
    output = bytearray()

    for block_index in range(1, blocks + 1):
        u = hmac.new(password, salt + block_index.to_bytes(4, "big"), hash_name).digest()
        mixed = bytearray(u)

        for _ in range(1, rounds):
            u = hmac.new(password, u, hash_name).digest()
            for i, value in enumerate(u):
                mixed[i] ^= value

        output.extend(mixed)

    return bytes(output[:dklen])


def hash_password(password: str, salt: bytes | None = None) -> Tuple[str, str]:
    if salt is None:
        salt = os.urandom(16)
    digest = _pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ROUNDS,
        dklen=32,
    )
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, digest_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False

    actual = _pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ROUNDS,
        dklen=32,
    )
    return hmac.compare_digest(actual, expected)


def _machine_secret() -> bytes:
    user = os.environ.get("USERNAME", "")
    node = platform.node()
    return f"{user}|{node}|ladder-client".encode("utf-8")


def _derive_key(secret: bytes, salt: bytes, length: int) -> bytes:
    return _pbkdf2_hmac(
        "sha256",
        secret,
        salt,
        CRED_ROUNDS,
        dklen=length,
    )


def encrypt_blob(payload: bytes) -> bytes:
    salt = os.urandom(16)
    stream_key = _derive_key(_machine_secret() + b":stream", salt, max(len(payload), 32))
    mac_key = _derive_key(_machine_secret() + b":mac", salt, 32)

    encrypted = bytes(payload[i] ^ stream_key[i] for i in range(len(payload)))
    mac = hmac.new(mac_key, encrypted, hashlib.sha256).digest()

    return CRED_MAGIC + salt + mac + encrypted


def decrypt_blob(blob: bytes) -> bytes:
    if len(blob) < len(CRED_MAGIC) + 16 + 32:
        raise ValueError("credential blob too short")
    if blob[: len(CRED_MAGIC)] != CRED_MAGIC:
        raise ValueError("invalid credential blob magic")

    pos = len(CRED_MAGIC)
    salt = blob[pos : pos + 16]
    pos += 16
    mac = blob[pos : pos + 32]
    pos += 32
    encrypted = blob[pos:]

    mac_key = _derive_key(_machine_secret() + b":mac", salt, 32)
    expected = hmac.new(mac_key, encrypted, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("credential blob integrity failed")

    stream_key = _derive_key(_machine_secret() + b":stream", salt, max(len(encrypted), 32))
    payload = bytes(encrypted[i] ^ stream_key[i] for i in range(len(encrypted)))
    return payload
