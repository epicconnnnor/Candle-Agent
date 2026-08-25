import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import Button from "./ui/Button";

const TABS = ["Model", "General", "Notifications"] as const;
type Tab = (typeof TABS)[number];

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 border-b border-border py-3 last:border-b-0">
      <span className="lbl">{label}</span>
      {children}
    </div>
  );
}

const field =
  "h-8 rounded-md border border-border bg-base px-2 text-[12px] font-mono text-text focus-visible:border-muted focus-visible:outline-none";

export default function SettingsModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("Model");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      className="fixed inset-0 z-50 flex items-center justify-center bg-base/80 p-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-lg border border-border bg-panel"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="lbl">Settings</span>
          <Button variant="ghost" onClick={onClose} aria-label="Close settings">
            Close
          </Button>
        </div>

        <div className="flex gap-1 border-b border-border px-4 pt-3">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`lbl -mb-px border-b-2 px-3 py-2 transition-colors ${
                tab === t ? "border-bull text-bull" : "border-transparent hover:text-text"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="px-4 py-2">
          {tab === "Model" && (
            <>
              <Row label="Provider">
                <select className={field} defaultValue="openai_compat">
                  <option>mock</option>
                  <option>openai_compat</option>
                </select>
              </Row>
              <Row label="Model">
                <input className={`${field} w-52`} defaultValue="deepseek-chat" />
              </Row>
              <Row label="Temperature">
                <input className={`${field} w-20 text-right`} defaultValue="0.20" />
              </Row>
              <Row label="Max tokens">
                <input className={`${field} w-20 text-right`} defaultValue="2048" />
              </Row>
            </>
          )}

          {tab === "General" && (
            <>
              <Row label="Analyze every Nth bar">
                <input className={`${field} w-20 text-right`} defaultValue="1" />
              </Row>
              <Row label="Risk per trade">
                <input className={`${field} w-20 text-right`} defaultValue="100" />
              </Row>
              <Row label="Reset zoom on new data">
                <input type="checkbox" defaultChecked className="accent-bull" />
              </Row>
            </>
          )}

          {tab === "Notifications" && (
            <>
              <Row label="Alert on decision change">
                <input type="checkbox" defaultChecked className="accent-bull" />
              </Row>
              <Row label="Alert on stop breach">
                <input type="checkbox" defaultChecked className="accent-bull" />
              </Row>
              <Row label="Sound">
                <input type="checkbox" className="accent-bull" />
              </Row>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
