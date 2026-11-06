import hashlib
import hmac

from pydantic import SecretStr


class Authenticator:
    def __init__(self, keys: list[SecretStr]) -> None:
        self.hashes = [hashlib.sha256(key.get_secret_value().encode()).hexdigest() for key in keys]
        if any(not key.get_secret_value() for key in keys):
            raise ValueError("API keys must not be empty")

    def authenticate(self, key: str | None) -> str | None:
        candidate = hashlib.sha256((key or "").encode()).hexdigest()
        valid = False
        for expected in self.hashes:
            valid |= hmac.compare_digest(candidate, expected)
        return candidate if key and valid else None
