# Architecture

[Home](../README.md) · [Technical guide](technical-guide.md) · [Installation](installation.md)

Design notes: the decisions, and what forced them. Feature descriptions live
in the README. Results live in [`results.md`](results.md); the scoring
layer's own reasoning lives in [`scoring-design.md`](scoring-design.md).

---

## The two-stage split

One prompt was tried first. The model jumped straight to "buy here" without
establishing what kind of market it was looking at, and the answers moved
between runs on the same bars.

Splitting it fixed more than prompt tuning ever did. **Stage 1 describes the
market and is forbidden to mention trades** — regime, cycle, strength, key
levels, a summary. **Stage 2 receives that diagnosis and decides.** The
diagnosis is not advice stage 2 may weigh; it is a commitment stage 2
inherits. Because stage 1 has already said "bull trend, these are the
levels", stage 2 cannot invent a short, and `consistency_errors()` rejects
the reply if it tries.

Routing falls out of the same commitment: `ROUTES` maps regime to playbook —
`bull_trend`/`bear_trend` to the trend prompt, `range`/`chop` to the range
one. Stage 2 never chooses its own strategy.

A real run shows the mechanism. AAPL, Wed 2026-08-26 09:30 ET, analysis 6908.
Stage 1 returned `bull_trend`, cycle `trend`, strength moderate, with six key
levels. Stage 2 was handed that and declined:

```
1. Trend is bullish, so only long entries are considered.
2. Last close is 310.38, near key level 310.48 (within 0.5*ATR).
3. However, price is in a pullback after a strong rally to 311.66.
4. No clear breakout or pullback entry signal at current bar.
5. Recent bars show indecision and low volume.
6. Setup is unclear, so no trade is the professional choice.
```

Step 1 is stage 1's commitment being honoured — direction was not up for
debate. Step 6 is the trend playbook's own gate: *"If the setup is unclear,
'no_trade' is the correct professional answer."* The checklist recorded
`trend_alignment: with_regime`, `level_proximity: at_level` — *"Close 310.38
is within 0.19 of key level 310.48"* — and `na` for the two nodes that need
an entry that does not exist.

The split has two costs, both real. It doubles the LLM calls: score run 11
measured **4,952 prompt tokens per analysis** across both stages. And a wrong
stage 1 poisons stage 2 rather than being caught by it — run 11's ten false
trend calls each handed stage 2 a premise it was then obliged to work within.

---

## The service split

This started as one script, and a slow LLM call blocked data ingestion while
it ran. Bars arriving during a 5-second analysis were simply not read.

Four processes now, over NATS JetStream: **ingest** (venue → bars),
**analyzer** (bars → two-stage analysis), **replay** (stored bars → analysis
at an as-of bound), **api** (REST, SSE, scoring). What each split buys:

- The analyzer can be restarted mid-session without dropping a candle;
  ingest keeps writing and JetStream holds the requests.
- Analyzers scale horizontally — a NATS queue group load-balances bars
  across replicas, so `--scale analyzer=3` needs no code change.
- The api holds *ephemeral* subscriptions and fans events out over SSE. A
  browser only cares about what happens while it is watching, so a
  disconnected client leaves nothing to replay.

The cost is that a durable bus is a second source of truth with its own
semantics. That is not theoretical: when the LLM key was rejected, the
analyzer's log read `redelivery #2`, then `#3`, then the messages exhausted
their delivery limit and were gone. The right behaviour, but it means "I
queued that" and "that will happen" are different statements.

---

## Bugs worth documenting

### Backoff keyed on connection instead of progress

**Symptom.** Roughly 24 reconnects in 30 seconds against a venue that
accepted the connection every time.

**Cause.** The retry counter reset when the socket opened. Subscribing to a
symbol a venue accepts but never sends data for connects successfully and
fails immediately after, so a connect-keyed counter sits at 1 forever and the
backoff degenerates into a hot loop.

**Fix.** The counter resets on `progress()` — an actual bar arriving — and on
nothing else.

**Generalises.** Reset a retry counter on evidence of the outcome you want,
not on a step toward it. A successful handshake is not a successful
subscription.

### BYO keys cannot traverse JetStream

**Symptom.** No crash. A design constraint discovered while adding
bring-your-own-key.

**Cause.** JetStream persists messages to disk. Publishing a request carrying
a visitor's API key writes that key into a stream file, which is exactly what
"never stored" is supposed to preclude.

**Fix.** With a visitor key, `/api/analyze` runs the analysis **inline** in
the api process and returns the result at 200. Without one it publishes and
returns 202, as before. The follow-up chat endpoint inherited the rule and
has no queued form at all — a 202 with no way to deliver the answer would be
a worse lie than a 400.

**Generalises.** Durability leaks. Before putting a secret on a transport,
ask what that transport writes down.

### No `PYTHONUNBUFFERED`

**Symptom.** A service failing and a service hung looked identical: no log
output from either.

**Cause.** Python buffers stdout when it is not a terminal. In a container
nothing appeared until the buffer filled or the process died, so the most
informative moment — the failure itself — was the one guaranteed to be
invisible.

**Fix.** `PYTHONUNBUFFERED=1` in the Dockerfile.

**Generalises.** Observability defaults differ between a terminal and a
container, and silence is only information once you have made it so.

### Demo bars indistinguishable from real ones — twice

**Symptom.** A chart with a y-axis stretched from 250 to 600 because a series
priced near 500 contained bars priced near 100. Found on AAPL, fixed, then
found again on MSFT: **160 Saturday bars** at 99–108 sitting among real
Thursday and Friday bars at 501–517.

**Cause.** The demo generator writes into the same `bars` table under real
symbol names. The primary key is `(symbol, interval, ts)`, so `INSERT OR
REPLACE` interleaves synthetic and real rows and silently overwrites. Nothing
on a row said where it came from.

**Fix, first time.** Delete the contaminated segment and refill from the
venue. Identified structurally — US equities have no Saturday bars — never by
price.

**Fix, second time.** A `source` column on `bars`, written from the feed's own
name, with every read defaulting to real data only. The difference showed
immediately: clearing BTCUSDT took one predicate, `delete_bars('BTCUSDT',
source=SYNTHETIC)`, removing **3,683 rows** with no forensics at all.

**Generalises.** The first fix removed the bad data. The second removed the
ambiguity. Only the second one holds.

### A fingerprint test that asked the code what to cover

**Symptom.** The prompt fingerprint had been incomplete twice — once globbing
every `*.txt` and sweeping in a prompt outside the analysis contract, once
hashing one of three validator gates. A test was added. To check it worked,
the fingerprint was deliberately narrowed to exclude `MIN_STOP_ATR`. **The
test passed.**

**Cause.** The test enumerated the constants by calling
`orchestrator.validator_gates()` — the function under test. Narrowing that
function narrowed the test's own expectations with it.

**Fix.** The test derives its list independently: constants from
`vars(schemas)`, prompts from the directory listing. Both historical mistakes
now fail the build — `1 failed, 14 passed` and `2 failed, 13 passed`. The
production code was also changed to cover prompts by *exclusion* rather than
by pattern, which fails safe: a new file is fingerprinted by default, and the
worst case is an unnecessary reset instead of a pooled sample that was
quietly two populations.

**Generalises.** A test that asks the code what it should cover cannot detect
the code covering too little.

---

## Measurement

**Row count is not sample size.** Score run 3 had 25 rows and **one**
independent window: consecutive decision bars with 30-bar forward windows
overlap almost entirely, so 25 rows carried roughly one window's worth of
information.

The fix is stride. At stride 30 with a 30-bar horizon, decision bars are 30
minutes apart and no two forward windows can overlap — run 6 recorded 32 rows
over 32 independent windows, run 11 recorded 24 over 24.

**The scorer refuses rather than reporting weakly.** Each grader has a stated
requirement; the trade grader wants 100 resolved trades over 30 independent
windows. Run 11 produced two trades, neither resolving, and the summary reads
`refused` with a sentence saying why. A win rate on four trades is noise no
matter what threshold is applied to it, so the honest output is a refusal,
not a number with a caveat.

**Store measures, derive labels.** Every threshold is applied to a continuous
quantity that is stored raw, so any threshold can be re-swept later without
re-running anything and without a single LLM call.

**Pre-register out-of-sample.** `cycle_amplitude_k = 1.10` was swept on MSFT
1m across 283 windows and written into `results.md` *before* the first
cycle-scored run existed. It predicted `compression` dominance; AAPL then
delivered 18 of 24, a 0.750 majority rate against 0.708 measured table-wide.
The counter-example is in the same document: the 3.0/2.0 abstention barriers
were swept on the very AAPL series they were then measured against, so run
11's `+0.133` lift is a fair model-versus-random comparison built on a
threshold that was not chosen in advance.

---

## The recurring defect class

Three times now, something has produced **a value that looked like evidence
and was not**.

**Demo bars.** A synthetic series under a real ticker rendered a normal
chart, fed a normal analysis, and produced a normal score row.

**`risk_reward`.** The validator read the model's own claimed ratio and
compared it to a floor, never deriving it from the entry, stop and target
sitting beside it. A reply claiming 3.0 on geometry worth 1.1 passed every
check. Six trade decisions were graded and published before anyone recomputed
them — one had claimed **2.0** against an actual **1.889**.

**Thresholds across asset classes.** Every scoring threshold was swept on
AAPL 1m. Point the scorer at a forex series and it does not fail; it produces
a complete summary, with gates and baselines, in the same shape as a real
result.

The shape is identical each time: **the output's type is unchanged, so
nothing downstream can object.** Failures that change the type — a 401, a
schema violation, a missing column — are cheap, because something catches
them immediately. Failures that preserve the type are expensive, because the
only thing that can catch them is a person noticing a number looks wrong, and
numbers rarely look wrong enough.

The countermeasure has been the same each time, and it is not more
validation. It is **provenance**: a `source` column on every bar, a
risk-reward derived rather than claimed, and a `calibrated_for` on the
thresholds — designed, not yet built. Validation asks whether a value is
well-formed. Provenance asks where it came from. Only the second catches a
well-formed lie.
