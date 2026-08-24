"""Message bus layer (NATS JetStream).

Subjects:
    bars.closed.<SYMBOL>        ingest -> analyzer      a bar just closed
    analysis.request.<SYMBOL>   api    -> analyzer      manual trigger
    analysis.completed.<SYMBOL> analyzer -> api (SSE)   result ready

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
ANALYSIS_REQUEST = "analysis.request.{symbol}"
ANALYSIS_COMPLETED = "analysis.completed.{symbol}"
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


def decode(msg) -> dict:
    return json.loads(msg.data.decode())
