"""Message bus layer (NATS JetStream).

Subjects:
    bars.closed.<SYMBOL>        ingest -> analyzer      a bar just closed
    analysis.request.<SYMBOL>   api    -> analyzer      manual trigger
    analysis.completed.<SYMBOL> analyzer -> api (SSE)   result ready

    snapshot.built.<SYMBOL>            -> api (SSE)   feature packet ready
    analysis.stage1.completed.<SYMBOL> -> api (SSE)   diagnosis validated

The two progress subjects exist so the UI can show stage 1 and stage 2
moving independently. `analysis.stage1.completed.<SYMBOL>` does NOT match
the `analysis.completed.>` wildcard - the second token differs - so no
existing consumer sees it by accident.

    ingest.control.subscribe    api    -> ingest        switch feed (request/reply)
    ingest.status.<SYMBOL>      ingest -> api (SSE)     connection state

The ingest.* subjects are core NATS, deliberately outside the JetStream
stream: a control request is only meaningful to a live ingest process,
and a status event is only meaningful to a browser currently watching.
Persisting either would just replay stale state after a restart.

JetStream gives us at-least-once delivery: the analyzer explicitly ACKs
each bar after processing it. If an analyzer pod dies mid-analysis, the
un-ACKed message is redelivered to another consumer in the queue group.
"""
import json

import nats
from nats.js.api import RetentionPolicy, StreamConfig

from . import config

STREAM = "MARKET"
SUBJECTS = ["bars.>", "analysis.>", "paper.>"]

BARS_CLOSED = "bars.closed.{symbol}"
INGEST_CONTROL = "ingest.control.subscribe"
INGEST_STATUS = "ingest.status.{symbol}"
ANALYSIS_REQUEST = "analysis.request.{symbol}"
ANALYSIS_COMPLETED = "analysis.completed.{symbol}"
SNAPSHOT_BUILT = "snapshot.built.{symbol}"
STAGE1_COMPLETED = "analysis.stage1.completed.{symbol}"

# progress events, keyed by the name the orchestrator emits
PROGRESS_SUBJECTS = {
    "snapshot.built": SNAPSHOT_BUILT,
    "analysis.stage1.completed": STAGE1_COMPLETED,
}
PAPER_UPDATE = "paper.update.{symbol}"


async def connect(startup_retries: int = 30, retry_delay_s: float = 2.0):
    """Connect and make sure the stream exists (idempotent).

    Retries the *initial* connection: in a distributed deployment there is
    no guaranteed start order, so a service must tolerate the bus not being
    up yet instead of crash-looping."""
    import asyncio

    last = None
    for i in range(startup_retries):
        try:
            nc = await nats.connect(
                config.NATS_URL,
                max_reconnect_attempts=-1,      # after connect: retry forever
                reconnect_time_wait=2,
            )
            break
        except Exception as e:
            last = e
            print(f"[bus] connect attempt {i + 1}/{startup_retries} failed ({e!r}), retrying in {retry_delay_s}s")
            await asyncio.sleep(retry_delay_s)
    else:
        raise ConnectionError(f"could not reach NATS at {config.NATS_URL}: {last!r}")
    js = nc.jetstream()
    cfg = StreamConfig(
        name=STREAM,
        subjects=SUBJECTS,
        retention=RetentionPolicy.LIMITS,
        max_msgs=100_000,
    )
    try:
        await js.add_stream(cfg)
    except Exception:
        await js.update_stream(cfg)
    return nc, js


async def publish(js, subject: str, payload: dict):
    await js.publish(subject, json.dumps(payload).encode())


async def publish_core(nc, subject: str, payload: dict):
    """Fire-and-forget publish outside JetStream (status, control)."""
    await nc.publish(subject, json.dumps(payload).encode())


def decode(msg) -> dict:
    return json.loads(msg.data.decode())
