# Trading a trend

Stage 2. Loaded when stage 1 diagnosed `bull_trend` or `bear_trend`.

## What you inherit

Stage 1's diagnosis is not a suggestion you weigh. It is settled. You
were routed here because the window passed the trend test, and your job
is to decide what to do about a trend — not to reconsider whether one
exists.

Concretely: **you may not produce a counter-trend entry.** In a
`bull_trend` you find a long or you decline. There is no short available
to you here. If the window looks to you like it is reversing, that is
not a short signal; that is a reason to decline.

## Where to enter

Not at the extreme. A trend that has just made a new high is at the
worst price it has offered, and an entry there has the full width of the
next pullback as adverse excursion before it works.

Enter on the pullback, at or near a level stage 1 named.

- Bull trend: a limit buy at or just above a support level below current
  price.
- Bear trend: a limit sell at or just below a resistance level above
  current price.

The entry must sit within 0.5 ATR of a named level. That is a hard
requirement, not a preference. An entry chosen from open space is a
price you invented, and nothing distinguishes it from any other price.

If no named level sits at a sensible distance, there is no entry. Say
`no_trade`.

## Where the stop goes

Beyond the structure the entry relies on, roughly 1.0 ATR from entry.

- Bull: below the swing low the support level is built on.
- Bear: above the swing high the resistance is built on.

Two failure modes, both common:

**Too tight.** A stop closer than about 0.5 ATR sits inside the
window's ordinary noise. It will be hit by a bar that means nothing.
Distance is not risk control if the distance is smaller than a typical
bar.

**Widened to fit.** A stop pushed out to 3 ATR makes almost any target
clear the risk-reward gate on paper, and means the loss, when it comes,
is three times what the sizing assumed. Widening the stop until the
arithmetic works is how a rejected trade becomes an accepted bad one.

This one has no answer of its own to record. `stop_placement` accepts
`beyond_swing`, `too_tight` or `na`, and a stop that is too wide is
still beyond the swing - so it reports `beyond_swing` and the damage
shows up in the risk-reward step instead. If the only stop that clears
the ratio is one the structure does not justify, the honest answer is
`no_trade`, not a wider stop.

## Where the target goes

At the next structural level in the direction of the trend, or at a
measured projection of the prior leg — whichever is nearer.

Then check the ratio:

```
risk_reward = |target - entry| / |entry - stop|
```

**This must be at least 1.5.** It is computed from your three prices,
not asserted. Whatever you write in the `risk_reward` field will be
recomputed from entry, stop and target, and a mismatch is an error.

If the ratio falls short, do not move the target further away to fix it.
The target is where price is plausibly going. Moving it to satisfy a
gate produces a number that passes and a trade that does not. Decline
instead.

## Declining

`no_trade` is the correct answer more often than not, and it is not a
failure. Decline when:

- Price is mid-trend with no pullback in progress — nothing to enter
  against.
- The nearest level is more than 0.5 ATR from any sensible entry.
- The stop that structure requires makes the ratio fall below 1.5.
- The trend is intact but stretched, and the next move is as likely to
  be a pullback as a continuation.

When you decline, name the gate that stopped you. "Risk-reward would be
1.2 against a 1.5 minimum" is a useful answer. "Conditions are unclear"
is not.
