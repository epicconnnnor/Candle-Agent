"""Secret scrubbing, request rate limiting, and transport checks.

The bring-your-own-key path has one hard rule: a visitor's API key is used
for exactly one upstream call and is never written anywhere - no database,
no log line, no error message, no trace. The helpers here are what make
that rule enforceable rather than aspirational.
"""
import re
import time
from collections import defaultdict, deque

# Anything shaped like a provider key, scrubbed even when we do not hold a
# copy to compare against - upstream errors often echo the key back.
KEY_PATTERN = re.compile(r"\b(sk|pk|api|key)[-_][A-Za-z0-9_\-]{12,}\b", re.IGNORECASE)

REDACTED = "***REDACTED***"

# below this length a "secret" is more likely to be a common substring;
# blindly replacing it would corrupt unrelated text
MIN_SECRET_LEN = 8


def scrub(text, *secrets: str | None) -> str:
    """Remove known secrets and key-shaped tokens from a string.

    Applied to everything that can escape the process: HTTP error bodies,
    exception text, log lines.
    """
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= MIN_SECRET_LEN:
            out = out.replace(secret, REDACTED)
    return KEY_PATTERN.sub(REDACTED, out)


class RateLimiter:
    """Fixed-capacity sliding window per key (an IP, here).

    In-memory and therefore per-process: with several api replicas the
    effective limit is per replica. That is deliberate - a shared counter
    would need Redis, and this exists to blunt abuse, not to meter billing.
    """

    def __init__(self, limit: int, window_s: float = 3600.0, max_tracked: int = 10_000):
        self.limit = limit
        self.window_s = window_s
        self.max_tracked = max_tracked
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits[key]
        cutoff = now - self.window_s
        while hits and hits[0] < cutoff:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
            return deque()
        return hits

    def check(self, key: str) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Records the hit when allowed."""
        if self.limit <= 0:                     # 0 disables the limit
            return True, 0

        now = time.time()
        hits = self._prune(key, now)

        if len(hits) >= self.limit:
            retry_after = int(self.window_s - (now - hits[0])) + 1
            return False, max(retry_after, 1)

        # bound memory: drop the coldest tracked keys rather than grow forever
        if key not in self._hits and len(self._hits) >= self.max_tracked:
            coldest = min(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0)
            self._hits.pop(coldest, None)

        self._hits[key].append(now)
        return True, 0

    def remaining(self, key: str) -> int:
        if self.limit <= 0:
            return -1
        return max(0, self.limit - len(self._prune(key, time.time())))


def client_ip(request, trust_proxy: bool) -> str:
    """The address to rate limit.

    X-Forwarded-For is trivially spoofable, so it is only honoured when the
    deployment says it sits behind a proxy that rewrites it. Otherwise a
    single attacker would get an unlimited number of buckets.
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return getattr(request.client, "host", None) or "unknown"


def is_secure(request) -> bool:
    """True when the request reached us over TLS, proxies included."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto.split(",")[0].strip().lower() == "https"


def is_loopback(request) -> bool:
    host = (request.url.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}
