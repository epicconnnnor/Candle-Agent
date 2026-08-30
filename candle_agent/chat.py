"""Follow-up conversation about a stored analysis.

Pure message assembly - no I/O - so the same logic runs from the API and
from a test. services/api.py owns the database read and the LLM call, the
same split as paper.py / services/paper_trader.py.

Nothing produced here is validated, and that is deliberate: a prose answer
has no schema to satisfy, and forcing JSON onto it would be decoration
rather than a check. Two things contain the cost of that choice. The
prompt forbids new trade calls, because a trade instruction from this path
would carry none of the validation the stored decision passed. And this
module writes nothing - a follow-up must never reach the analyses table,
or it would pollute the very population the scorer reads.
"""
import json
import pathlib

PROMPT = pathlib.Path(__file__).parent / "prompts" / "followup_chat.txt"

# The bar table is the expensive part of the context and it is pinned once
# at the top rather than repeated per turn, so a long conversation grows by
# the questions and answers alone. Six turns keeps a follow-up worth about
# a third of an analysis; past that the earliest exchanges have usually
# stopped informing the answer anyway.
MAX_HISTORY_TURNS = 6
MAX_MESSAGE_CHARS = 2000


def system_prompt() -> str:
    return PROMPT.read_text(encoding="utf-8")


def trim_history(history) -> list[dict]:
    """The last MAX_HISTORY_TURNS exchanges, oldest first.

    A turn is a user message and the answer to it, so the cap is applied
    in messages rather than pairs - an unanswered last question should not
    cost an extra exchange of context.
    """
    clean = []
    for turn in history or []:
        role = "assistant" if turn.get("role") in ("agent", "assistant") else "user"
        text = (turn.get("text") or "").strip()
        if text:
            clean.append({"role": role, "content": text[:MAX_MESSAGE_CHARS]})
    return clean[-(MAX_HISTORY_TURNS * 2):]


def analysis_context(analysis: dict) -> str:
    """The stored verdict, as the model originally emitted it.

    Rendered from the stored JSON rather than re-summarised, so the model
    is answering about the row that exists rather than a paraphrase of it.
    """
    stage1 = analysis.get("stage1") or {}
    stage2 = analysis.get("stage2") or {}
    lines = [
        f"Analysis stored at bar ts={analysis.get('ts')} "
        f"({analysis.get('interval', '1m')}), model {analysis.get('model')}.",
    ]
    if analysis.get("price_at") is not None:
        lines.append(f"Price at analysis: {analysis['price_at']} "
                     f"(ATR14 {analysis.get('atr_at')})")
    lines.append(f"Stage 1 diagnosis: {json.dumps(stage1)}")
    lines.append(f"Stage 2 decision:  {json.dumps(stage2)}")
    return "\n".join(lines)


def build_messages(symbol: str, analysis: dict, packet: dict,
                   history, question: str) -> list[dict]:
    """The full message list for one follow-up turn.

    Order matters for cost: the analysis and the bar table are one system
    message pinned ahead of the conversation, so a provider that caches
    prompt prefixes can reuse them across every turn.
    """
    context = (
        f"{system_prompt()}\n\n"
        f"--- symbol {symbol} ---\n"
        f"{analysis_context(analysis)}\n\n"
        f"EMA20: {packet['ema20']} (price {packet['price_vs_ema']})\n"
        f"ATR14: {packet['atr14']}\n"
        f"last close: {packet['last_close']}\n\n"
        f"Bar table (K1 = newest closed bar):\n{packet['bar_table']}"
    )
    messages = [{"role": "system", "content": context}]
    messages.extend(trim_history(history))
    messages.append({"role": "user", "content": question.strip()[:MAX_MESSAGE_CHARS]})
    return messages
