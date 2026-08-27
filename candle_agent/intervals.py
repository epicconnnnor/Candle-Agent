"""Supported kline intervals.

The names mirror Binance's exactly, so an interval string can be
interpolated straight into a stream name with no translation table.
"""

# interval -> bar duration in milliseconds
INTERVALS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

DEFAULT = "1m"

SUPPORTED = ", ".join(INTERVALS)


def is_valid(interval: str) -> bool:
    return interval in INTERVALS


def to_ms(interval: str) -> int:
    """Bar duration in ms. Raises ValueError on an unsupported interval."""
    try:
        return INTERVALS[interval]
    except KeyError:
        raise ValueError(
            f"unsupported interval {interval!r}; supported: {SUPPORTED}"
        ) from None
