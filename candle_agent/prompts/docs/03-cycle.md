# Reading the cycle

Stage 1. The cycle answers a different question from the regime.

## Two independent axes

`regime` says what shape the window has. `cycle` says whether the
amplitude is growing or shrinking. They are orthogonal — a range can be
compressing or expanding, and knowing which changes what happens next
more than the regime label does.

## The two measures

**Amplitude ratio A**

```
A = envelope of the recent half / envelope of the earlier half
```

Split the window in half. Compute `(max high - min low)` for each half.
Divide recent by earlier. Above 1.0 means the market is opening up;
below means it is closing down.

**Efficiency E** — the same figure computed for the trend test.

**A caveat about A.** The ratio you can compute is the recent half of the
window against the earlier half. The scoring layer computes a different
one: the envelope of the *forward* window against the envelope of the
window you were shown. They answer the same question over different
spans, and k was swept on the scorer's version, not yours.

Nothing about that is fixable from here - you cannot see forward, which
is the entire point. Apply the ratio you can actually compute and do not
try to guess at the other. A cycle call is a claim about what the window
in front of you is doing; whether that claim survives contact with the
next thirty bars is the grader's question, not yours.

## The four labels

| Label | Condition | Meaning |
|---|---|---|
| `compression` | A < 1/k, **or** 1/k ≤ A < k with E < 0.35 | Narrowing, or steady but going nowhere |
| `trend` | 1/k ≤ A < k and E ≥ 0.35 | Steady amplitude, direction persisting |
| `breakout` | A ≥ k and E ≥ 0.35 | Amplitude expanding with direction |
| `exhaustion` | A ≥ k and E < 0.35 | Expanding without direction — churn |

k = 1.10, so the lower bound is 1/k = 0.9091. Use 1/k, not a rounded
0.91: the boundary is defined by k and nothing else.

`compression` covers two cases, and the second is the one most often
missed. A window whose amplitude is steady but whose price is going
nowhere is compression, not a trend — steady is not the same as
directional. On short timeframes this is the single most common state,
so a table that had no rule for it would push you toward `trend`
whenever the amplitude simply failed to move.

Note that `exhaustion` and `breakout` share an expansion condition and
differ only on efficiency. Both are volatile. One is going somewhere.

## Why compression matters most

Most windows compress. On short timeframes it is the common state, and
labelling it correctly is worth more than catching the rare breakout,
because compression is where the wrong trade gets taken. Price is quiet,
the boundaries are close, and any entry has too little room to pay.

Do not reach for a more dramatic label to make the analysis interesting.
`compression` on a quiet window is the correct answer and the useful
one.

## Independence

Compute the cycle from A and E directly. Do not infer it from the regime
you just assigned. A bull trend can be compressing — advancing while the
bars get smaller — and that is a real and informative state. Deriving
one label from the other throws that away.
