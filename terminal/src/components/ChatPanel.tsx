import { useRef, useState } from "react";
import { ArrowUp, Trash2 } from "lucide-react";
import Button from "./ui/Button";
import Card from "./ui/Card";
import { ApiError, askFollowUp } from "../api/client";
import type { ChatMessage } from "../types";

interface Props {
  symbol: string;
  /** A visitor's own key, when they have supplied one. */
  apiKey?: string | null;
  /** No stored analysis means nothing to ask about yet. */
  hasAnalysis: boolean;
}

export default function ChatPanel({ symbol, apiKey, hasAnalysis }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(0);

  const send = async () => {
    const text = draft.trim();
    if (!text || pending) return;

    // the question goes up immediately; the answer is awaited
    const asked: ChatMessage = { id: nextId.current++, role: "user", text };
    const history = messages.map((m) => ({ role: m.role, text: m.text }));
    setMessages((m) => [...m, asked]);
    setDraft("");
    setPending(true);
    setError(null);

    try {
      const res = await askFollowUp(symbol, text, history, apiKey);
      setMessages((m) => [
        ...m,
        { id: nextId.current++, role: "agent", text: res.reply },
      ]);
    } catch (e) {
      // the question stays on screen - retyping it to retry is worse than
      // leaving it there with the reason it failed underneath
      setError(
        e instanceof ApiError
          ? e.detail || e.message
          : "The follow-up could not be sent.",
      );
    } finally {
      setPending(false);
    }
  };

  const idle = !hasAnalysis
    ? "Run an analysis first — a follow-up explains a verdict that already exists."
    : "Ask about the current diagnosis, the levels, or what would invalidate it.";

  return (
    <Card
      title="Follow-up"
      action={
        <Button
          variant="ghost"
          onClick={() => {
            setMessages([]);
            setError(null);
          }}
          disabled={!messages.length}
        >
          <Trash2 size={16} />
          Clear
        </Button>
      }
    >
      <div className="max-h-56 min-h-20 overflow-y-auto">
        {messages.length === 0 ? (
          <p className="font-sans text-[13px] leading-snug text-muted">{idle}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {messages.map((m) => (
              <li key={m.id} className="flex gap-3">
                <span
                  className={`font-sans text-[13px] shrink-0 ${
                    m.role === "agent" ? "text-bull" : "text-muted"
                  }`}
                >
                  {m.role === "user" ? "You" : "Agent"}
                </span>
                <span className="font-sans text-[13px] leading-snug text-text whitespace-pre-wrap">
                  {m.text}
                </span>
              </li>
            ))}
            {pending && (
              <li className="flex gap-3">
                <span className="font-sans text-[13px] shrink-0 text-bull">Agent</span>
                <span className="font-sans text-[13px] leading-snug text-muted">
                  thinking…
                </span>
              </li>
            )}
          </ul>
        )}
      </div>

      {error && (
        <p className="mt-3 font-sans text-[13px] leading-snug text-bear">{error}</p>
      )}

      {/* the input keeps its own outline: it is a control, not a section */}
      <div className="mt-4 flex items-center gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder={hasAnalysis ? "Ask a follow-up" : "No analysis yet"}
          aria-label="Follow-up message"
          disabled={!hasAnalysis || pending}
          className="h-[34px] min-w-0 flex-1 rounded-md border border-ctl-border bg-base px-3
                     font-sans text-[13px] placeholder:text-muted
                     focus-visible:border-muted focus-visible:outline-none
                     disabled:opacity-50"
        />
        <Button
          variant="primary"
          onClick={send}
          disabled={!draft.trim() || !hasAnalysis || pending}
        >
          <ArrowUp size={16} />
          Send
        </Button>
      </div>
    </Card>
  );
}
