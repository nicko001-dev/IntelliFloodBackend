"""Limiteur de débit minimal en mémoire (par IP) pour les endpoints publics en écriture.

Suffisant pour le prototype (un seul processus). En production multi-instances, utiliser un
proxy (nginx, Cloudflare) ou Redis.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import get_settings


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: int, name: str):
        self.max_calls, self.per_seconds, self.name = max_calls, per_seconds, name
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _client_ip(self, request: Request) -> str:
        if get_settings().trust_proxy:
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def __call__(self, request: Request) -> None:
        if get_settings().environment == "test":
            return
        ip, now = self._client_ip(request), time.monotonic()
        with self._lock:
            hits = self._hits[ip]
            while hits and now - hits[0] > self.per_seconds:
                hits.popleft()
            if len(hits) >= self.max_calls:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de requêtes, réessayez plus tard")
            hits.append(now)
