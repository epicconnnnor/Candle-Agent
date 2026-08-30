# Trading a range

Stage 2. Loaded when stage 1 diagnosed `range`.

## The one rule

**Enter only at the edges.** Never in the middle.

A range trade works because price has turned at a boundary before and
may turn there again. That is the entire edge. Away from the boundary
there is no edge, only a guess about which way the next bar goes.

Concretely: your entry must sit within 0.5 ATR of a level stage 1 named.
Not near the middle, not at a price that looks like it might hold. At a
level, measured.

An entry at mid-range is the single most common error here, and it is
worse than it looks: it is a coin flip that costs the same as a
considered trade.

## The two setups

**Fade support** — price near the lower boundary.
```
entry   limit buy at or just above support
stop    below the support level, ~1.0 ATR from entry
target  at or below the opposite boundary
```

**Fade resistance** — price near the upper boundary.
```
entry   limit sell at or just below resistance
stop    above the resistance level, ~1.0 ATR from entry
target  at or above the opposite boundary
```

The target belongs inside the range, not at the far edge exactly. Price
that reaches a boundary often turns before touching it. Aim for the near
side of the opposite boundary.

## Direction is not yours to choose

In a range, direction is decided by position. Price near support means
long. Price near resistance means short. There is no directional view to
form — the structure assigns it.

If price is at neither boundary, neither direction is available, and the
answer is `no_trade`.

## Risk-reward

```
risk_reward = |target - entry| / |entry - stop|
```

**At least 1.5.** Recomputed from your three prices.

In a range this ratio is mostly determined by the envelope. A range 2.5
ATR wide, entered at one edge with a 1.0 ATR stop, offers about 1.5 to
the far side — the minimum, exactly. A narrower range cannot produce a
qualifying trade at all, which is why anything under 2.5 ATR was
classified `chop` rather than `range`.

If the arithmetic falls short, decline. Do not narrow the stop to fix
it; a stop inside the noise will be hit.

## What breaks a range trade

**Entering mid-range.** Covered above. The most common and most costly.

**Trading a range with one clear boundary.** If support is well tested
and resistance is a single spike, you have half a structure. Fading the
tested side is defensible. Fading the invented side is not.

**Fading a boundary that just broke.** If the most recent bars closed
beyond a level, that level is not holding. A range whose edge has given
way is a range in the process of ceasing to be one. Decline and let the
next analysis reclassify it.

**Ignoring the cycle.** A compressing range is narrowing toward its
middle, and the boundaries you would fade are moving. A range at
`compression` deserves a higher bar for entry, and often `no_trade`.

## Declining

Decline when price sits mid-range, when the near boundary is untested,
when the ratio falls short, or when the edge has just broken.

Name the gate. "Price is 1.4 ATR from the nearest level, outside the 0.5
ATR requirement" tells the reader what happened. "No clear setup" does
not.
