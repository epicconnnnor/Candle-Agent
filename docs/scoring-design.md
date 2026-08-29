# Scoring layer

For every analysis in a replay run: what actually happened next?

Three graders read the same forward window. They exist separately because
their sample sizes differ by an order of magnitude — 25 analyses of which
23 are `no_trade` give you 2 trade outcomes but 25 regime verdicts.

| grader | question | rows per 25 analyses |
| --- | --- | --- |
| entries | did target or stop come first | 2 |
| abstentions | was declining right — did a payable move exist at all | 23 |
| diagnosis | was "range" actually a range | 25 |

That ratio, not the trade P&L, is why the layer is worth building before
there is a bigger sample.

## Where it lives

- `candle_agent/scoring.py` — pure functions over dicts, no I/O.
- `candle_agent/services/scorer.py` — the database shell. Same split as
  `paper.py` / `services/paper_trader.py`.
- `POST /api/score`, `GET /api/score/{id}` — synchronous, no NATS, no
  container. Replay needed a service because it is slow, costly and
  stateful; scoring is none of those.

**On demand only.** Scoring is never triggered by replay completing. Every
threshold below is a judgement call, so re-running with different
parameters is the normal case, and coupling the two would make the
parameters feel fixed when they are the whole point.

Fills are not re-implemented: `paper.on_bar` already owns the fill
predicates and the same-bar rule, and its docstring already promised to
drive "the historical backtest harness later". This is that harness. A
second copy would drift from the live paper trader and quietly invalidate
every comparison between paper results and scores.

## The no-lookahead constraint, mirrored

`test_no_lookahead.py` asserts an analysis cannot read forward. Scoring is
the exact mirror and `test_scoring.py` asserts it cannot read backward:

- `db.bars_after` uses `ts > ?`, strictly — the decision bar is history.
- The anchor comes from the analysis's own stored `price_at` / `atr_at`,
  never recomputed from the table, so the scorer's window cannot drift
  from the one the analysis was actually formed against. `price_at` is
  NULL on pre-`restore-analysis` rows; those fall back to the bar's close
  and are marked `anchor_source = 'derived'` rather than mixed in
  silently.
- Scoring writes only to score tables. `recent_bars` reads `bars`, so
  there is no path by which a score can reach a prompt. Structural, not
  conventional.

The guard carries a vacuity check, like the replay one: mutating bars at
or before the decision must change nothing; mutating bars after it must
change the result. Without the second half, a scorer that ignored the
bars table entirely would pass.

That test is asserted on the score **row**, not the summary — the
baselines below deliberately survey every bar in the table, so mutating
the past does legitimately move those.

## Prerequisite: the replay stride

Landed first, as its own commit, because the headline statistics are
near-worthless without it.

25 consecutive 1m analyses scored over a 30-bar window share 29 of every
30 bars. They are worth **one** independent observation, not 25. No care
in the scorer fixes a sample collected that densely.

`stride` publishes every Nth bar instead of every one (default 1, so
existing callers are unchanged). It changes what is *published*, never
what the model *reads*: the analyzer takes its history from
`db.recent_bars(as_of_ts=...)`, which reads the bars table, and every bar
is still stored. A stride-10 run analyses bars 30, 40, 50, … and the
analysis of bar 50 still sees a contiguous 21..50.

`tests/test_stride.py` asserts contiguity at every decision point in a
strided run, and separately — through the real analysis path — that bars
41..49 reach the prompt while bars after 50 do not. The second half is
the vacuity guard: "the model sees everything" would satisfy the first.

## Horizon

`horizon_bars = 30`. One number, so the three graders cross-tabulate on
the same window.

It is the only non-invented number available: `MIN_BARS = 30`, and
`build_feature_packet(n_recent=30)` shows the model 30 bars. A verdict
formed on 30 bars of structure is judged on 30 bars of consequence.
Symmetry is not proof, but every alternative is a round number or one
tuned until the results looked good.

Sanity check for trades: with a stop near 1×ATR and RR ≥ 1.5, the
double-barrier expected hitting time for a driftless walk with per-bar
σ ≈ 0.7 ATR is `a·b/σ²` ≈ 1.0 × 1.5 / 0.49 ≈ **3 bars**. So 30 resolves
the large majority, and the timeout rate becomes a diagnostic about the
geometry rather than a hole in the sample: many timeouts means the stops
are too wide for the horizon the strategy implicitly trades on.

Everything is denominated in ATR and bar counts, so the design is
interval-agnostic. The one thing that is not scale-free is friction —
fills are frictionless, inherited from `paper.py`, and at 1m with an
ATR-sized stop real fees are a material fraction of 1R.

**Horizon is also a hole in the prompt.** `stage1_diagnose.txt` says
"diagnose the current market regime" and never says over what horizon, so
any single N imposes one the model was never given. The secondary
horizons (10 and 60) are free arithmetic and are stored in
`horizons_json`; if the diagnoses are right at 10 and wrong at 60, that is
a finding about the prompt, not the model.

## Grader 1 — entries

| outcome | meaning |
| --- | --- |
| `target` | target reached first |
| `stop` | stop reached first, or same bar (pessimistic rule) |
| `timeout` | filled, neither barrier within the horizon **from fill** |
| `unfilled` | never filled within the fill window |
| `insufficient_data` | not enough bars exist after the decision |

Two clocks: `fill_window_bars` (20, inherited from
`paper.PENDING_TTL_BARS` so there is one TTL in the codebase) measured
from the decision, and `horizon_bars` measured from the **fill** — a
trade should not be scored as a timeout for filling late. Setting
`fill_window_bars` above the shared TTL is refused rather than silently
ignored, because `paper.on_bar` would expire the order first.

The binary outcome is worthless on 2 trades, so the continuous measures
carry the weight:

- `r_multiple` for resolved trades.
- `mtm_r` — mark-to-market R at the horizon end. A timeout at +0.9R and
  one at −0.9R are not the same event.
- `trade_mae_r` / `trade_mfe_r` — excursions from entry, in R. A win that
  went −0.95R first is a different animal from one that never traded
  against you.
- `entry_distance_atr` for `unfilled` — nearest approach to the limit.
  Separates "missed by 0.05 ATR" from "the level was a fantasy".
- `same_bar_ambiguous` — the pessimistic rule is a guess; this measures
  how often it was load-bearing.

## Grader 2 — was declining right

No counterfactual trade is constructed. Inventing one means inventing a
direction, a level and a stop — three arbitrary choices stacked. Instead:
**did a payable move exist at all?**

Anchored at `price_at`, in `atr_at` units, over the horizon:

- long side: did price reach **+1.5 ATR** before **−1.0 ATR**?
- short side: did it reach **−1.5 ATR** before **+1.0 ATR**?

The 1.5 and 1.0 are read off the stage-2 prompts (`risk_reward must be
>= 1.5`, stop "roughly 1x ATR14 from entry"), so the scorer holds the
model to the geometry the model was told to use.

The two sides are mutually exclusive by construction — reaching +1.5
means passing +1.0 first, which stops the short side out — so the outcome
is a clean label: `correct`, `miss_long`, `miss_short`,
`insufficient_data`.

Two qualifiers keep it from being blunt:

- **`miss_aligned`** — a model that diagnosed `bull_trend`, was offered a
  paying long and declined is failing on its own terms. One that
  diagnosed `bull_trend` while the market paid a short was wrong about
  direction, but refusing a counter-trend trade was correct *given that
  diagnosis*; that error belongs to grader 3. Counting both identically
  double-charges the same mistake.
- **`distance_to_nearest_level_atr`** — the range playbook allows entries
  only near the diagnosed `key_levels`, so a payable move from mid-range
  was never a takeable trade. The headline is aligned misses *at* a
  tradeable level; everything else stays visible underneath.

### The baseline, which matters more than the miss rate

`scoring.baselines()` runs the same barrier test over **every bar in the
table**, not just decision bars. If 40% of arbitrary bars pay and the
model's `no_trade` bars pay 40% of the time, its abstention carries no
information; if they pay 12%, it is selecting.

`lift = 1 − (miss_rate / base_rate)`. Zero LLM calls, and one side of the
comparison has thousands of samples. The same call also returns the
realized-regime distribution, which is the majority-class baseline grader
3 needs.

## Grader 3 — the diagnosis

The most measurable, and the reason the layer is worth building now: 25
usable rows from 25 analyses, no assumptions about trading at all.

The realized regime comes from three pure-price measures over the window:

- **efficiency** — `|Δclose| / Σ|Δclose|`, in [0,1]. High means it
  travelled in a line. The path starts at the decision close, so the
  first step is the move into the window rather than between two bars
  inside it.
- **displacement** — `(close_N − anchor) / atr_at`. Signed.
- **envelope** — `(max high − min low) / atr_at`.

```
efficiency >= 0.35 and |displacement| >= 1.5  ->  bull_trend / bear_trend
otherwise, envelope >= 2.5                    ->  range
otherwise                                     ->  chop
```

The range/chop split is not invented: the prompts define chop as having
"no tradeable structure", so the split is amplitude — 2.5 ATR is a 1.5
target plus a 1.0 stop, i.e. a range is an envelope the strategy's own
trade fits inside. Same geometry as grader 2, so the two agree rather
than quietly measuring different things.

The 4×4 confusion matrix collapses to five classes, because 16 cells over
25 rows is noise:

| verdict | meaning | cost |
| --- | --- | --- |
| `exact` | claimed == realized | — |
| `amplitude_error` | range ↔ chop | mild; nearly the same trade |
| `false_trend` | claimed trend, got range/chop | overtrading risk |
| `missed_trend` | claimed range/chop, got trend | under-participation |
| `inversion` | bull ↔ bear | worst; every downstream decision inherits it |

**Store measures, derive labels.** `fwd_efficiency`, `fwd_return_atr` and
`fwd_envelope_atr` are stored raw at every horizon, so any threshold can
be re-swept later without re-running anything and without a single LLM
call. This is the rule for the whole layer: never store only the label.

Raw accuracy is never reported alone — always against the majority-class
baseline. If 60% of windows are mechanically `range` and the model says
`range` 60% of the time, 60% accuracy is worth nothing.

## Levels — deliberately not built

Each analysis emits up to 6 `key_levels`, which would be ~150 falsifiable
claims from 25 analyses: the largest sample in the system. It is also the
most work, and the first three graders should land before scope widens.

`distance_to_nearest_level_atr` is computed and stored now, because
grader 2 needs it anyway. That is the hook; the grader is future work.

## Schema

Two tables. Parameters travel with the scores — they are arbitrary and
will be swept, and without them a stored score cannot be interpreted,
only misread.

`score_runs` — `replay_run_id` (nullable; live analyses can be scored
too), `symbol`, `interval`, `scorer_version`, `params_json`,
`created_at`, `analyses_scored`, `analyses_incomplete`,
`independent_windows`, `summary_json`, `status`, `detail`.

`analysis_scores` — one row per analysis, `UNIQUE (score_run_id,
analysis_id)`:

- provenance — `analysis_id`, `symbol`, `interval`, `bar_ts`, `price_at`,
  `atr_at`, `anchor_source`, `bars_available`, `window_end_ts`,
  `complete`. `price_at`/`atr_at` are denormalised on purpose: a score
  row must stay reproducible on its own and must not change if the
  analyses row is later amended.
- forward facts — `fwd_mfe_atr`, `fwd_mae_atr`, `fwd_return_atr`,
  `fwd_efficiency`, `fwd_envelope_atr`, `horizons_json`.
- the claim — `claimed_regime`, `claimed_strength`, `decision`,
  `confidence`, `entry`, `stop`, `target`,
  `distance_to_nearest_level_atr`.
- grader 1 — `trade_outcome`, `filled_ts`, `bars_to_fill`, `exit_ts`,
  `bars_to_exit`, `r_multiple`, `mtm_r`, `trade_mae_r`, `trade_mfe_r`,
  `entry_distance_atr`, `same_bar_ambiguous`.
- grader 2 — `abstention_outcome`, `missed_direction`, `miss_aligned`,
  `bars_to_payoff`.
- grader 3 — `realized_regime`, `regime_verdict`.

Re-scoring creates a **new** run; old rows are never overwritten. They
are not stale — they are answers to a different question.

## What the statistics can carry

### Read this before any of the numbers

Consecutive analyses are not independent observations.
`scoring.independent_windows()` computes the largest set of
non-overlapping forward windows, and it is reported on the summary **and
on every section of it**, so no count can be read as if it were an
independent sample. `REQUIREMENTS` gates every headline number on both
row count and independent windows, and when a gate fails the summary says
so in a plain sentence that names the fix:

> Cannot support a regime accuracy against the majority-class baseline:
> 25 rows, but only 1 independent window (5 needed). Overlapping forward
> windows inflate the row count without adding information — raise the
> replay stride.

For a 25-bar dense run at horizon 30, that is the honest verdict: it can
show the pipeline works end to end and produce case studies, but it
cannot support a statistical claim.

### Meaningful once the sample is strided

| statistic | why it survives a small n |
| --- | --- |
| abstention miss rate vs base rate | one side is the entire bar table; a binomial test against a precisely known base rate has real power for a large effect |
| `fwd_mfe_atr` on `no_trade` bars vs all bars | same asymmetry, and continuous rather than a bit |
| regime verdict counts, 5 collapsed classes | report **counts, not percentages** — percentages on 25 invite over-reading |
| regime accuracy vs majority-class baseline | a single comparison; 4/25 against a 15/25 baseline needs no statistics |
| timeout / unfilled / same-bar-ambiguous rates | mechanical diagnostics about the geometry, not claims about skill |
| the 2 entries, narrated with MAE/MFE | case studies, labelled as such, not a win rate |

### Needs hundreds

| statistic | roughly |
| --- | --- |
| win rate, expectancy, total R | ~400 trades to separate 45% from 55% |
| confidence calibration | ~50 per level ⇒ 150+ |
| strength ↔ displacement correlation | 50+ |
| full 4×4 regime matrix | ~30 per cell ⇒ ~500 |
| Cohen's κ with a usable CI | ~200 for ±0.1 |
| per-regime conditional accuracy | 120+ |
| model-vs-model comparison | double the above, both arms |

## Every arbitrary choice

Three of the thirteen flatter the model; two penalise it. The rest are
neutral or inherited. They are listed rather than buried because the
scorer is only useful if you know which way its thumb rests.

| # | choice | bias |
| --- | --- | --- |
| 1 | `horizon_bars = 30`, anchored to `MIN_BARS`, itself arbitrary | neutral — 10/60 stored |
| 2 | `trend_efficiency = 0.35`. The most arbitrary number here; 0.3–0.4 is convention, not derivation | **unresolved** |
| 3 | `trend_displacement_atr = 1.5`, borrowed from the RR gate, which is about trade geometry not regime | neutral |
| 4 | `range_envelope_atr = 2.5` = target + stop, which assumes entry exactly at an extreme | neutral |
| 5 | Benchmark anchored at the decision close, not a pullback — easier than any real order | **against the model** |
| 6 | ATR frozen at decision time: comparable, but stale if volatility shifts mid-window | neutral |
| 7 | Pessimistic same-bar rule, inherited | **against the model** |
| 8 | `fill_window_bars = 20`, inherited from `PENDING_TTL_BARS`, never independently justified | inherited |
| 9 | Trades resolved from fill, other graders from the decision bar — breaks strict window comparability | **toward the model** |
| 10 | Counter-diagnosis misses treated as weaker evidence. A charitable reading; "a miss is a miss" is defensible | **toward the model** |
| 11 | `level_proximity_atr = 0.5` — pure convenience | neutral |
| 12 | Collapsing 16 cells into 5, ranked with inversion worst — a claim about cost made before there is evidence about cost | neutral |
| 13 | No fees or slippage, inherited. At 1m with an ATR-sized stop, friction is a material fraction of 1R | **toward the model** |

`REQUIREMENTS` — the row and independent-window gates on each headline
number — is a fourteenth set of judgement calls, kept in code next to the
thing it gates rather than in a report template.

## Two defects the tests caught

Recorded because both were silent-wrong-answer bugs, not crashes.

**`stride=0` was read as "every bar".** The first draft used
`int(req.get("stride", 1) or 1)`, so a caller mistake became a
plausible-looking dense run. Now refused. Found by the parametrised
validation test.

**The anchor-precedence rule was never actually pinned.** A mutation that
re-read the anchor from the bars table instead of the stored `price_at`
passed the entire suite, because `scorer.run` only fetches a decision bar
when `price_at` is NULL — so the mutation was unreachable end to end.
`test_the_stored_anchor_wins_over_any_bar_handed_to_the_scorer` now pins
it directly. The lesson generalises: an invariant only enforced on an
unreachable path is not enforced.
