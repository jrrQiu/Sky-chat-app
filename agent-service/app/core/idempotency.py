import time


class IdempotencyStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._keys: dict[str, str] = {}
        self._expires: dict[str, float] = {}
        self.ttl_seconds = ttl_seconds

    def get_or_create(self, key: str, value: str) -> tuple[str, bool]:
        self._prune()
        existing = self._keys.get(key)
        if existing is not None:
            self._expires[key] = time.time() + self.ttl_seconds
            return existing, False

        self._keys[key] = value
        self._expires[key] = time.time() + self.ttl_seconds
        return value, True

    def _prune(self) -> None:
        now = time.time()
        expired = [key for key, expires in self._expires.items() if expires <= now]
        for key in expired:
            self._keys.pop(key, None)
            self._expires.pop(key, None)


idempotency_store = IdempotencyStore()
