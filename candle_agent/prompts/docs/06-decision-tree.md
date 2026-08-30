# The decision sequence

Both stages. The order in which questions get asked, and what each
answer forecloses.

## Two principles

**Unclear means wait.** At every node, if the answer is not clear, the
answer is not "probably yes." It is `no_trade`. A forced decision on an
unclear structure is worse than no decision, because it costs the same
as a considered one.

**Earlier answers bind later ones.** A question answered in stage 1 is
not reopened in stage 2. The regime is settled before any entry is
considered, and the entry must be consistent with it. This is what stops
a bullish diagnosis producing a short.

---

## Stage 1 — describe only

No entries, stops, targets or trade language. Stage 1 says what is
there.

### §1 Is there enough data?

Fewer than 30 bars, or bars with gaps in them → `no_trade`, and stop.

### §2 Does the window pass the trend test?

Efficiency ≥ 0.35 **and** |displacement| ≥ 1.5 ATR. Compute both.

- Yes → `bull_trend` or `bear_trend` by the sign of displacement.
- No → §3.

### §3 Is the envelope large enough to trade?

Envelope ≥ 2.5 ATR.

- Yes → `range`.
- No → `chop`.

### §4 Which way is amplitude moving?

From A and E: `compression`, `breakout`, `trend`, or `exhaustion`.
Computed independently of §2 and §3.

### §5 Which levels can be evidenced?

Prices that price reached and turned from more than once. Report those.
Do not fill unused slots.

---

## Stage 2 — decide

Routed by the regime from §2/§3. `bull_trend` and `bear_trend` take the
trend playbook; `range` and `chop` both take the range playbook.

`chop` is not short-circuited on the way in. It arrives here and is
declined here, by the same gates as everything else - a chop window has
no evidenced levels to enter near, so §7 stops it. Declining through the
gates rather than by construction means the refusal is recorded and
checkable, instead of being an outcome nothing observed.

### §6 Trend alignment

Is the entry you are considering with the diagnosed direction?

- With → continue.
- Against → **stop. `no_trade`.** A counter-trend entry is not available
  in a trend playbook. In a range, direction is set by which boundary
  price is near, not by a view.

### §7 Level proximity

Is the entry within 0.5 ATR of a level named in §5?

- At a level → continue.
- Mid-range → **stop. `no_trade`.** An entry away from structure has no
  reason to be at that price rather than any other.

### §8 Stop placement

Is the stop beyond the structure the entry depends on, and roughly 1.0
ATR from entry?

- Beyond the swing → continue.
- Inside the noise, under ~0.5 ATR → **stop. `no_trade`.** It will be
  hit by an ordinary bar.

### §9 Risk-reward

```
risk_reward = |target - entry| / |entry - stop|
```

- ≥ 1.5 → continue to output.
- < 1.5 → **stop. `no_trade`.**

Do not move the target to clear this gate. The target is a claim about
where price is going; adjusting it to satisfy arithmetic makes the claim
false and the number meaningless. This value is recomputed from your
three prices, so an asserted ratio that disagrees with the geometry is
an error, not a rounding difference.

---

## Recording the path

Report each node you reached with the answer you gave. A node you never
reached, because an earlier one stopped you, is `na`.

Three of these are checked against the numbers. `level_proximity` is
recomputed from your entry and the levels; `stop_placement` from your
stop and the ATR; `risk_reward` from all three prices. An answer that
contradicts the arithmetic is a validation error.

`level_proximity` is answerable even when you decline. If there is no
entry, measure from the last close. It is the one node that always has
something to measure, and it must never be `na` when §5 named a level.

## When declining, name the gate

The useful part of a `no_trade` is which question stopped it.

Good: "Risk-reward is 1.18 against a 1.5 minimum."
Good: "Entry would be 1.9 ATR from the nearest level."
Not useful: "No clear setup." "Conditions are unfavourable."

The first two can be checked, aggregated and learned from. The last two
cannot.
