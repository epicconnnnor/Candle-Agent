# Recognising a trend

Stage 1. Read this before calling any regime a trend.

## The test

A trend is not "price went up." It is a specific, measurable shape. Two
conditions must both hold over the bars you were given. If either fails,
it is not a trend, however strongly it may read as one.

**1. Efficiency ≥ 0.35**

Efficiency is how much of the travelling got you somewhere:

```
efficiency = |close_last - close_first| / Σ |close_i - close_i-1|
```

The numerator is net movement. The denominator is total movement,
summing the absolute change bar to bar. A perfectly straight advance
scores 1.0. Price that ends where it started scores 0.0.

At 0.35, roughly a third of the motion was directional and two thirds
was noise. Below that, the series wandered to its destination and the
destination was incidental.

**2. |displacement| ≥ 1.5 ATR**

```
displacement = (close_last - close_first) / atr14
```

Net movement measured in units of one bar's typical range. Under 1.5
ATR, the whole move is smaller than two ordinary bars. That is not a
trend; that is drift.

**Both. Not either.**

A series can travel 4 ATR while zig-zagging violently — high
displacement, low efficiency. That is a wide range, not a trend. A
series can advance in a perfectly clean line by 0.4 ATR — high
efficiency, no displacement. That is quiet, not trending.

## How to apply it

You have a table of bars. Do the arithmetic.

1. Take `close` from the first and last rows. Subtract. That is net
   movement, with sign.
2. Walk the closes in order, summing the absolute difference between
   each and the one before. That is total movement.
3. Divide net by total. That is efficiency.
4. Divide net by ATR14. That is displacement.
5. Compare both against the thresholds.

Do not estimate these by looking at the shape of the numbers. Compute
them. A series that "obviously" trends frequently scores 0.15.

## Direction

Only once both tests pass:

- displacement positive → `bull_trend`
- displacement negative → `bear_trend`

## What is not a trend

These are the mistakes that produce a false trend call. Each one is a
pattern that feels directional and fails the test.

**A single large bar inside a quiet series.** One 3-ATR bar surrounded
by 29 small ones lifts displacement and destroys efficiency. Net
movement came from one bar; the other 29 went nowhere. Not a trend.

**A move that has already finished.** Price rose 2 ATR in the first
third of the window and has chopped sideways since. Displacement passes,
because it is measured first-to-last. Efficiency fails, because the
chopping added a lot of denominator. The correct label is a range that
happens to sit above where it started.

**Consecutive same-colour bars.** Six green bars in a row is a striking
pattern and says nothing about either test. Six small green bars can
total less than 1 ATR.

**A recovery.** Price fell 2 ATR then rose 2 ATR. Displacement is
approximately zero and efficiency is near zero, because the denominator
counted both legs. Both directions were real; the series went nowhere.

**Slope in the last few bars.** The most recent bars are the ones you
notice, and they are only a fraction of the window. The test is computed
over all of them.

## When it fails

If either condition fails, the regime is `range` or `chop`, decided in
the range recognition document. There is no partial credit and no
"weak trend" label. The strength field describes a trend that already
passed the test; it is not a way to record one that did not.

Say what the numbers say. A window that scores efficiency 0.22 and
displacement 0.9 ATR is a range, even when every instinct reads it as a
developing uptrend.
