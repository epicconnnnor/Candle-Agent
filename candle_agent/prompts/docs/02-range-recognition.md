# Recognising a range

Stage 1. Read this once the trend test has failed.

## Where this starts

A window that fails either trend condition is not automatically a range.
It is either a range or chop, and the difference is amplitude: whether
there is enough room in it to fit a trade.

## The test

**Envelope ≥ 2.5 ATR → `range`. Otherwise → `chop`.**

```
envelope = (max high - min low) / atr14
```

The full span from the highest high to the lowest low across the window,
in units of one bar's typical range.

The threshold is not arbitrary. A range trade risks about 1.0 ATR and
targets about 1.5 ATR, so it needs 2.5 ATR of room to exist at all.
Below that, the boundaries are close enough together that a stop at one
edge and a target at the other are the same price.

That is what `chop` means here. Not "messy" — too small to trade.

## How to apply it

1. Scan all `high` values, take the maximum.
2. Scan all `low` values, take the minimum.
3. Subtract, divide by ATR14.
4. Compare to 2.5.

Use highs and lows, not closes. The boundaries of a range are where
price actually reached, and stops get hit by wicks.

## Naming the boundaries

Once the window is a range, the levels matter more than the label. A
range with vague boundaries is not tradeable regardless of its envelope.

A boundary is a price that price reached and turned away from more than
once. Look for:

- Highs within roughly 0.2 ATR of each other, at different points in the
  window. That is resistance.
- Lows within roughly 0.2 ATR of each other. That is support.
- A price that acted as resistance and then as support, or the reverse.

Report the boundaries you can actually locate. Two well-evidenced levels
are worth more than six invented ones. Every level you name becomes a
place stage 2 may enter, and a level that price never respected is a
place stage 2 will enter for no reason.

If you cannot find a boundary that price tested twice, say so. A range
with no defensible edges is closer to chop in practice, whatever the
envelope says.

## Strength

- `strong` — boundaries tested repeatedly, price turns cleanly at both,
  the envelope is well above 2.5.
- `moderate` — boundaries identifiable, some overshoot on tests.
- `weak` — envelope barely clears 2.5, or one boundary is much clearer
  than the other.

Strength describes the quality of the structure, not your confidence in
having found it. Confidence is a separate field.

## Common errors

**Calling a range chop because it looks messy.** Messy is not the test.
Compute the envelope. A 4 ATR range with heavy overlap is still a range,
and it is one of the better ones to trade — wide, with clear edges.

**Calling chop a range because you can draw lines on it.** Two lines can
be drawn through any series. If the envelope is 1.8 ATR, the lines are
1.8 ATR apart and there is no trade between them.

**Reporting the extreme bars as the boundaries.** A single spike high
that price never revisited is not resistance. It is one bar. The
boundary is where price turned away more than once.

**Reporting six levels because the schema allows six.** Report what you
found. An unused slot costs nothing; a fabricated level costs a trade.
