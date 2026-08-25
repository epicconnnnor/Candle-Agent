import { useRef, useState } from "react";
import { ArrowUp, Trash2 } from "lucide-react";
import Button from "./ui/Button";
import type { ChatMessage } from "../types";

const CANNED = [
  "The 20 EMA has held three retests; losing it invalidates the long thesis.",
  "Risk is 2.75 points to entry, so a 2R target sits just under the resistance shelf.",
  "Volume contracted through the pullback, which is consistent with continuation.",
  "If price closes below the swing low the regime call flips to range.",
];

export default function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const nextId = useRef(0);

  const send = () => {
    const text = draft.trim();
    if (!text) return;
    const reply = CANNED[nextId.current % CANNED.length];
    setMessages((m) => [
      ...m,
      { id: nextId.current++, role: "user", text },
      { id: nextId.current++, role: "agent", text: reply },
    ]);
    setDraft("");
  };

  return (
    <section className="rounded-lg border border-border bg-panel">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="lbl">Follow-up</span>
        <Button variant="ghost" onClick={() => setMessages([])} disabled={!messages.length}>
          <Trash2 size={16} />
          Clear
        </Button>
      </div>

      <div className="max-h-56 min-h-24 overflow-y-auto px-4 py-3">
        {messages.length === 0 ? (
          <p className="text-[13px] text-muted">
            Ask about the current diagnosis, the levels, or what would invalidate it.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {messages.map((m) => (
              <li key={m.id} className="flex gap-3">
                <span className={`lbl mt-1 shrink-0 ${m.role === "agent" ? "text-bull" : ""}`}>
                  {m.role === "user" ? "You" : "Agent"}
                </span>
                <span className="text-[13px] leading-snug">{m.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-border p-3">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask a follow-up"
          aria-label="Follow-up message"
          className="h-8 min-w-0 flex-1 rounded-md border border-border bg-base px-3 text-[13px]
                     placeholder:text-muted focus-visible:border-muted focus-visible:outline-none"
        />
        <Button variant="primary" onClick={send} disabled={!draft.trim()}>
          <ArrowUp size={16} />
          Send
        </Button>
      </div>
    </section>
  );
}
