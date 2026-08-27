"""Merged symbol catalogue with a 24h in-memory cache.

Every registered source is asked for its symbols and the results are
merged. The list is large (Binance alone is ~1500 pairs, Alpaca ~11000
assets) and changes a few times a week, so it is fetched once per
SYMBOLS_TTL_S. The cache is process-local: each api replica keeps its own.

Partial failure is not total failure. If Binance is geo-blocked but
Alpaca answers, the catalogue is whatever answered, plus a record of what
did not - the api reports that alongside the symbols instead of failing
the whole request.
"""
import asyncio
import time

from . import config, sources
from .sources.base import SourceError, SymbolInfo


class SymbolsUnavailable(RuntimeError):
    """No source could supply a catalogue and nothing is cached."""

    def __init__(self, message: str, errors: dict[str, str] | None = None):
        super().__init__(message)
        self.errors = errors or {}


_cache: list[SymbolInfo] | None = None
_errors: dict[str, str] = {}
_fetched_at: float = 0.0
_retry_at: float = 0.0
_lock = asyncio.Lock()
_index: dict[str, SymbolInfo] = {}


async def _fetch_all() -> tuple[list[SymbolInfo], dict[str, str]]:
    registry = sources.get_registry()
    results = await asyncio.gather(
        *(src.list_symbols() for src in registry.values()),
        return_exceptions=True,
    )

    merged: list[SymbolInfo] = []
    errors: dict[str, str] = {}
    for name, result in zip(registry, results):
        if isinstance(result, BaseException):
            message = (result.message if isinstance(result, SourceError)
                       else f"{result!r}")
            errors[name] = message
            print(f"[symbols] {name} unavailable: {message}")
        else:
            merged.extend(result)

    merged.sort(key=lambda s: (s.source, s.symbol))
    return merged, errors


async def refresh() -> list[SymbolInfo]:
    """Force a re-fetch, bypassing the TTL."""
    return await get_symbols(force=True)


async def get_symbols(force: bool = False) -> list[SymbolInfo]:
    """The cached catalogue, refreshing when stale."""
    global _cache, _errors, _fetched_at, _retry_at, _index

    async with _lock:
        now = time.time()
        fresh = _cache is not None and (now - _fetched_at) < config.SYMBOLS_TTL_S
        if fresh and not force:
            return _cache
        # a failed refresh must not turn into a request-per-call stampede
        # against a source that is down
        if _cache is not None and not force and now < _retry_at:
            return _cache

        merged, errors = await _fetch_all()

        if not merged:
            _errors = errors
            if _cache is not None:
                _retry_at = now + config.SYMBOLS_RETRY_S
                print("[symbols] refresh produced nothing, serving cached copy")
                return _cache
            raise SymbolsUnavailable(
                "No data source could supply a symbol list: "
                + ("; ".join(f"{k}: {v}" for k, v in errors.items()) or "no sources registered"),
                errors,
            )

        _cache, _errors, _fetched_at = merged, errors, now
        _retry_at = now + config.SYMBOLS_RETRY_S if errors else 0.0
        _index = {s.symbol: s for s in merged}
        return _cache


async def lookup(symbol: str) -> SymbolInfo | None:
    """The catalogue entry for a symbol, or None if it is not listed."""
    await get_symbols()
    return _index.get(symbol)


def last_errors() -> dict[str, str]:
    """Sources that failed on the most recent fetch, name -> reason."""
    return dict(_errors)


def reset() -> None:
    """Drop the cache. For tests."""
    global _cache, _errors, _fetched_at, _retry_at, _index
    _cache, _errors, _fetched_at, _retry_at, _index = None, {}, 0.0, 0.0, {}
