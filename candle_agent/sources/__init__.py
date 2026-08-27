"""Source registry.

Built from whichever credentials are present. Binance needs none, so it
is always there; Alpaca joins only when both keys are set. Missing Alpaca
keys are a logged fact at startup, not a crash - a crypto-only deployment
is a perfectly good deployment.
"""
from .. import config
from .base import (CRYPTO, EQUITY, AuthFailed, Bar, DataSource, RegionBlocked,
                   SourceError, SourceUnavailable, StreamClosed, SymbolInfo,
                   UnknownSymbol, aggregate)
from .alpaca import AlpacaSource
from .binance import BinanceSource
from .demo import DemoSource

__all__ = [
    "CRYPTO", "EQUITY", "AuthFailed", "Bar", "DataSource", "RegionBlocked",
    "SourceError", "SourceUnavailable", "StreamClosed", "SymbolInfo",
    "UnknownSymbol", "aggregate", "AlpacaSource", "BinanceSource",
    "DemoSource", "build_registry", "get_registry", "get", "names", "reset",
]

_registry: dict[str, DataSource] | None = None


def build_registry(on_event=None) -> dict[str, DataSource]:
    """Instantiate every source whose credentials are available."""
    registry: dict[str, DataSource] = {
        BinanceSource.name: BinanceSource(on_event=on_event),
    }

    if config.ALPACA_KEY_ID and config.ALPACA_SECRET_KEY:
        registry[AlpacaSource.name] = AlpacaSource(
            config.ALPACA_KEY_ID, config.ALPACA_SECRET_KEY, on_event=on_event)
        print("[sources] registered: " + ", ".join(registry))
    else:
        print("[sources] registered: " + ", ".join(registry)
              + " (Alpaca disabled: set ALPACA_KEY_ID and ALPACA_SECRET_KEY "
                "to enable stocks)")

    return registry


def get_registry(on_event=None) -> dict[str, DataSource]:
    """Process-wide registry, built once on first use."""
    global _registry
    if _registry is None:
        _registry = build_registry(on_event=on_event)
    return _registry


def get(name: str) -> DataSource:
    registry = get_registry()
    try:
        return registry[name]
    except KeyError:
        raise UnknownSymbol(
            f"unknown source {name!r}; registered: {', '.join(registry)}"
        ) from None


def names() -> list[str]:
    return list(get_registry())


def reset(registry: dict[str, DataSource] | None = None) -> None:
    """Replace the registry. For tests, which inject a fake source."""
    global _registry
    _registry = registry
