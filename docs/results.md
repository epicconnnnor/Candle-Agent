# Results — score run 6

**On this sample the model does not beat a constant-range predictor, and its
abstentions are worse than random.**

Regime accuracy was **0.781** (25 of 32 windows). Always answering "range"
would have scored **0.969** (31 of 32). The model was 18.8 points worse
than a predictor that ignores the chart entirely.

The shape of the error is sharper than the headline. The model said
"range" 26 times and was right 25 of them. It called a trend 6 times —
twice bull, four times bear — and was wrong **all six**. Every one of
those windows realized as a range. It is right when it agrees with the
base rate and 0-for-6 whenever it departs from it, which is the precise
sense in which it adds nothing over the constant predictor.

Abstentions were worse than chance. Of 26 `no_trade` decisions, 14 sat in
front of a move that would have paid — a **miss rate of 0.538** against a
base rate of **0.384** measured over 1,926 bars of the same series. The
lift is **−0.402**: the bars the model declined paid *more* often than
bars picked at random. Of those 14 misses only one agreed with the
model's own diagnosis, and none sat within half an ATR of a level it had
itself named, so they cannot be excused as playbook-conformant refusals.

## Gates

Two of the three gates passed. Both passing results are negative.

| grader | rows | independent windows | gate | result |
| --- | --- | --- | --- | --- |
| regime | 32 | 32 | **passed** | accuracy 0.781 vs 0.969 baseline — `beats_majority: false` |
| abstention | 26 | 26 | **passed** | miss rate 0.538 vs base 0.384, lift −0.402 |
| trade | 4 | 4 | **refused** | needs 100 rows and 30 independent windows |

Independence equals the row count in every grader — stride 30 produced
decision bars 30 minutes apart, so no two forward windows overlap. That
is what the earlier dense runs could not deliver: score run 3 had 25 rows
worth 1 independent window.

The trade grader is refused and its numbers should not be read as
results. For the record: six trade decisions, of which one hit target
(+3.06R), three stopped (−1.00R each), one timed out at −0.655R
mark-to-market, and one never filled — missing its limit by 0.10 ATR.
Total across the four resolved rows is **+0.059R** on a win rate of 0.25,
which is one lucky trade away from any conclusion you like.

## Provenance

Score run 6 pools replay runs 2 and 5 — AAPL 1m, `deepseek-chat`, stride
30 on both. 33 analyses, 32 scored, 1 incomplete: Mon 08-24 19:30 had 29
forward bars where 50 were needed, so the session end truncated it and it
was refused rather than scored across the overnight gap.

Parameters: regime and trade over 30 bars, abstention over 10 at 3.0/2.0
ATR barriers, `scorer_version` 1. The two horizons are not
cross-tabulable — they are different windows on the same decision.

Cost: 105,665 tokens (96,686 prompt, 8,979 completion) over 33 analyses,
3.8s average latency.

## Pre-registered: the cycle grader's amplitude band

Recorded **before** the first cycle-scored run, so it cannot be tuned to a
result afterwards.

`cycle_amplitude_k = 1.10`, swept with `sweep_cycle_k()` over **MSFT 1m,
283 windows** — deliberately a different instrument from the AAPL series
it will be used to score. That is the one thing the abstention barriers
below got wrong, and it is not worth repeating.

| k | majority baseline | breakout | compression | exhaustion | trend |
| --- | --- | --- | --- | --- | --- |
| **1.10** | **0.569** | 26 | 161 | 95 | 1 |
| 1.30 | 0.728 | 25 | 206 | 46 | 6 |
| 1.50 | 0.820 | 24 | 232 | 19 | 8 |
| 2.00 | 0.852 | 19 | 241 | 10 | 13 |
| 2.50 | 0.866 | 7 | 245 | 6 | 25 |

1.10 minimises the majority-class baseline, which is the only property
worth optimising — a k that lets one label take 90% of windows produces a
baseline nothing can beat, which is precisely the trap the regime grader
fell into above.

**A limitation the sweep exposed, recorded rather than tuned away.** Only
about 11% of 1m windows are directional at `trend_efficiency` 0.35, so
`trend` and `breakout` split a small population however k is chosen, and
at k=1.10 `trend` is nearly unreachable — 1 window of 283. On 1m equities
in a rangebound stretch this is effectively a **three-class grader**. The
amplitude ratio is stored raw, so k can be re-swept on any new instrument
or interval without an LLM call.

## Caveats

**The market barely moved.** 31 of 32 scored windows realized as `range`;
one was a bull trend. Across the whole scored span AAPL travelled 308.23
to 315.38 — **2.32%** over three sessions. A constant-range predictor is
close to unbeatable in that regime, so the 0.969 baseline says as much
about the week as about the model. The scored windows were also more
rangebound than the surrounding series: over all 1,806 baseline windows
the majority-class rate was 0.873, against 0.969 in the sample.

**Three sessions, not five.** The 32 windows fall on 2026-08-24 (12),
2026-08-25 (9) and 2026-08-27 (11). Run 5's range extended into 08-26 but
its 22-analysis cap stopped it first, and 08-28 was never replayed. The
*baseline* population does span five sessions — 2,000 bars, 08-24 to
08-28 — so the sample and the baseline it is compared against do not
cover the same days.

**One symbol, one interval, one model.** Nothing here generalises past
AAPL at 1m under `deepseek-chat`.

**The abstention barriers were calibrated on this same data.** 3.0/2.0
over 10 bars was chosen by sweeping this AAPL series to land the base
rate near 0.34. The measured 0.384 is therefore not out-of-sample, and
the lift inherits that. It is a fair comparison between the model and
random bars *on this series*; it is not evidence about a threshold chosen
in advance.

**Fills are frictionless.** No fees, no slippage, and the pessimistic
same-bar rule was never load-bearing here (`same_bar_ambiguous: 0`).

**164 baseline windows were skipped** because a session boundary cut them
short. That is correct behaviour — a 30-bar window spanning an overnight
gap makes every barrier trivially reachable — but it means the baseline
is computed on 1,926 of 2,000 bars.

## What would change the answer

**More symbols.** One instrument cannot separate "the model is weak" from
"the model is weak on AAPL". Three or four names across different sectors
and volatilities is the cheapest way to find out which.

**A period containing trends.** The strongest caveat above is that the
sample had almost no trends to detect, so the regime grader mostly
measured whether the model could refrain from crying wolf — and it cried
wolf six times. A stretch with real directional moves would test the
other half: whether it catches a trend when there is one. On this sample
that was tested exactly once, and it missed (row 24, a realized bull
trend it called `range`).

**More days.** 32 windows over three sessions is enough to clear the
regime and abstention gates but not enough to say anything conditional —
accuracy given a claimed trend rests on 6 rows.

**Trade rows.** The gate wants 100 resolved trades over 30 independent
windows. The model resolved 4 trades in 33 analyses (12.1%), so roughly
825 analyses would be needed — about 21 sessions at stride 30, and on the
order of 2.7M tokens. Loosening the requirement is not the answer; a win
rate on 4 trades is noise regardless of what a threshold says.

## Per-row

`ret`, `eff` and `env` are the 30-bar forward return, Kaufman efficiency
and envelope, all in ATR-at-decision units. `outcome` is the abstention
verdict for `no_trade` rows and the trade verdict otherwise.

```
 #   session   time    claimed   realized       verdict    decision     outcome      R    ret   eff   env
 1 Mon 08-24  13:30      range      range         exact    no_trade     correct          2.87  0.20  4.01
 2 Mon 08-24  14:00 bull_trend      range   false_trend    no_trade     correct          1.15  0.10  3.12
 3 Mon 08-24  14:30      range      range         exact    no_trade     correct         -0.30  0.04  2.93
 4 Mon 08-24  15:00      range      range         exact    no_trade  miss_short         -4.17  0.25  8.64
 5 Mon 08-24  15:30      range      range         exact    no_trade     correct         -2.33  0.15  4.17
 6 Mon 08-24  16:00      range      range         exact    no_trade  miss_short          0.79  0.04  4.68
 7 Mon 08-24  16:30      range      range         exact    no_trade   miss_long          7.46  0.29 12.25
 8 Mon 08-24  17:00      range      range         exact   buy_limit        stop  -1.00  -3.17  0.19  5.61
 9 Mon 08-24  17:30      range      range         exact    no_trade  miss_short         -1.62  0.08  5.08
10 Mon 08-24  18:00      range      range         exact    no_trade  miss_short         -2.64  0.15  4.27
11 Mon 08-24  18:30      range      range         exact    no_trade  miss_short         -3.15  0.11 11.15
12 Mon 08-24  19:00 bear_trend      range   false_trend market_sell        stop  -1.00  -1.35  0.10  4.62
13 Mon 08-24  19:30      range          —             —    no_trade  insufficient_data
14 Tue 08-25  13:15      range      range         exact    no_trade   miss_long         -1.20  0.03 15.93
15 Tue 08-25  13:59 bear_trend      range   false_trend    no_trade     correct         -0.83  0.07  2.74
16 Tue 08-25  14:29      range      range         exact    no_trade   miss_long         -6.00  0.25  9.42
17 Tue 08-25  14:59 bear_trend      range   false_trend market_sell     timeout          1.13  0.07  4.67
18 Tue 08-25  15:29      range      range         exact    no_trade     correct          1.93  0.11  4.13
19 Tue 08-25  15:59      range      range         exact    no_trade     correct          2.10  0.14  5.73
20 Tue 08-25  16:29      range      range         exact    no_trade  miss_short          0.71  0.04  3.82
21 Tue 08-25  16:59      range      range         exact  sell_limit    unfilled         -4.15  0.28  4.35
22 Tue 08-25  17:29      range      range         exact   buy_limit        stop  -1.00  -1.72  0.09  5.22
23 Thu 08-27  14:00      range      range         exact    no_trade  miss_short         -2.43  0.14  6.60
24 Thu 08-27  14:30      range bull_trend  missed_trend    no_trade   miss_long          8.94  0.50  9.41
25 Thu 08-27  15:00 bull_trend      range   false_trend    no_trade     correct          2.02  0.15  4.52
26 Thu 08-27  15:30      range      range         exact    no_trade     correct          2.53  0.13  4.50
27 Thu 08-27  16:00      range      range         exact  sell_limit      target   3.06  -2.47  0.16  4.82
28 Thu 08-27  16:30      range      range         exact    no_trade     correct          3.57  0.19  6.43
29 Thu 08-27  17:00      range      range         exact    no_trade     correct          1.91  0.12  3.94
30 Thu 08-27  17:30      range      range         exact    no_trade  miss_short         -4.04  0.21  5.57
31 Thu 08-27  18:00 bear_trend      range   false_trend    no_trade  miss_short         -4.36  0.32  5.14
32 Thu 08-27  18:30      range      range         exact    no_trade   miss_long         -0.62  0.03  8.17
33 Thu 08-27  19:00      range      range         exact    no_trade     correct         -3.00  0.17  6.43
```

### Regime confusion

| claimed | realized | n |
| --- | --- | --- |
| range | range | 25 |
| bear_trend | range | 4 |
| bull_trend | range | 2 |
| range | bull_trend | 1 |

Six `false_trend`, one `missed_trend`, no inversions — the model never got
the direction of a real trend backwards, because there was only one real
trend and it called that one a range.

### The six trade decisions

```
Mon 08-24 17:00   buy_limit  312.770 / stop 312.590 / target 313.110  -> stop      R=-1.00  mae=-1.139  mfe=+0.778
Mon 08-24 19:00 market_sell  311.425 / stop 311.555 / target 311.150  -> stop      R=-1.00  mae=-1.107  mfe=+0.607
Tue 08-25 14:59 market_sell  309.070 / stop 309.530 / target 308.380  -> timeout   mtm=-0.655  mae=-0.869  mfe=+1.690
Tue 08-25 16:59  sell_limit  310.040 / stop 310.140 / target 309.510  -> unfilled  missed by 0.10 ATR
Tue 08-25 17:29   buy_limit  309.605 / stop 309.515 / target 309.860  -> stop      R=-1.00  mae=-2.056  mfe=+2.389
Thu 08-27 16:00  sell_limit  314.950 / stop 315.120 / target 314.430  -> target    R=+3.06   mae=-0.118  mfe=+3.235
```

**Geometry verified, after the fact.** These six were re-checked against
`|target-entry| / |entry-stop|` once the validator was fixed to derive
risk-reward instead of trusting the model's own field. **All six clear the
1.5 playbook floor**, so nothing above is withdrawn. Two details belong on
the record anyway. The 08-24 17:00 buy_limit reported `risk_reward: 2.0`
against geometry worth **1.889** - the only material gap between a claimed
and an actual ratio in the sample, and undetectable at the time because
nothing recomputed it. And the 08-25 14:59 short sits exactly on the floor,
clearing 1.5 by 6e-14; it passes on intent rather than on rounding luck only
because the check carries a tolerance.

Note also that the fix changes the analysis population: a decision with a
ratio between 1.0 and 1.5 would have validated before and is rejected now,
so runs produced under the corrected validator are not poolable with this
one.

Two of these are worth noting even though the grader is refused. The
08-25 17:29 buy_limit reached +2.39R in its favour before reversing into
its stop — a −1.00R row that spent most of its life winning. And the
08-25 16:59 sell_limit never filled, missing by a tenth of an ATR: not a
loss, not a win, and invisible to any statistic that only counts
resolved trades. Both are the reason the excursion columns exist.
