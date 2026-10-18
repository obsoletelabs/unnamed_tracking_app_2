"""
src/core/crypto.py

Symmetric encryption for secrets we must store at rest (e.g. a PSN npsso
token) but never need to search/index â€” Fernet (AES-128-CBC + HMAC) via
`settings.SECRET_KEY`.

For a fresh installation only, generate a key for `.env`'s SECRET_KEY with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from src.core.config import settings
from src.core.fernet_key import validate_fernet_key


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(validate_fernet_key(settings.SECRET_KEY).encode())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt stored secret. Restore the original matching SECRET_KEY "
            "from backup; generating a new key cannot recover existing encrypted secrets."
        ) from exc
