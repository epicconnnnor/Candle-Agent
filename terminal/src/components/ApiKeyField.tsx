import { useState } from "react";
import { Check, Eye, EyeOff, X } from "lucide-react";
import { testKey } from "../api/client";

interface Props {
  apiKey: string;
  onChange: (key: string) => void;
  labelClass: string;
  fieldClass: string;
  controlWidth: string;
}

type Test =
  | { state: "idle" }
  | { state: "testing" }
  | { state: "done"; valid: boolean; detail: string };

/**
 * Bring-your-own-key entry.
 *
 * The value lives in React state in the parent and nowhere else: no
 * localStorage, no sessionStorage, no cookie, no server record. A reload
 * clears it, by design. There is no "remember my key" and there will not
 * be one.
 */
export default function ApiKeyField({
  apiKey, onChange, labelClass, fieldClass, controlWidth,
}: Props) {
  const [visible, setVisible] = useState(false);
  const [test, setTest] = useState<Test>({ state: "idle" });

  const check = async () => {
    setTest({ state: "testing" });
    try {
      const res = await testKey(apiKey);
      setTest({ state: "done", valid: res.valid, detail: res.detail });
    } catch (e) {
      setTest({
        state: "done",
        valid: false,
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-6">
        <span className={labelClass}>API key</span>
        <div className={`${controlWidth} shrink-0`}>
          <div className="relative">
            <input
              type={visible ? "text" : "password"}
              value={apiKey}
              autoComplete="off"
              spellCheck={false}
              // not "sk-..." - that reads as a masked real key already set
              placeholder="Paste your key"
              aria-label="Your LLM API key"
              onChange={(e) => {
                onChange(e.target.value);
                setTest({ state: "idle" });
              }}
              className={`${fieldClass} w-full pr-9`}
            />
            <button
              type="button"
              onClick={() => setVisible((v) => !v)}
              aria-label={visible ? "Hide API key" : "Show API key"}
              className="absolute top-1/2 right-2 -translate-y-1/2 text-label
                         transition-colors hover:text-text"
            >
              {visible ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
        </div>
      </div>

      <div className="flex items-start justify-between gap-6">
        <p className="max-w-[220px] font-sans text-[12px] leading-snug text-label">
          Your key stays in this browser tab and is sent only with your
          analysis requests. It is never stored on the server.
        </p>
        <div className={`${controlWidth} shrink-0`}>
          <button
            type="button"
            onClick={check}
            disabled={!apiKey || test.state === "testing"}
            className="h-[30px] rounded-md border border-ctl-border bg-ctl px-3 font-sans
                       text-[13px] font-medium text-ctl-text transition-colors
                       hover:border-ctl-border-hover hover:text-text
                       disabled:cursor-not-allowed disabled:opacity-40"
          >
            {test.state === "testing" ? "Testing…" : "Test key"}
          </button>

          {test.state === "done" && (
            <p
              className={`mt-2 flex items-start gap-1.5 font-sans text-[12px] leading-snug
                          ${test.valid ? "text-bull" : "text-bear"}`}
            >
              {test.valid ? (
                <Check size={13} className="mt-0.5 shrink-0" />
              ) : (
                <X size={13} className="mt-0.5 shrink-0" />
              )}
              <span>{test.valid ? "Key accepted." : test.detail}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
