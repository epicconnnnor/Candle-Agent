import { useState } from "react";
import { Check, Eye, EyeOff, X } from "lucide-react";
import { testKey } from "../api/client";
import { storageAvailable } from "../apiKeyStorage";

interface Props {
  apiKey: string;
  onChange: (key: string) => void;
  /** Opt-in persistence. Off unless the user has asked for it. */
  remember: boolean;
  onRemember: (remember: boolean) => void;
  /** Clears the field and anything written to this browser. */
  onForget: () => void;
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
 * By default the value lives in React state in the parent and nowhere
 * else, and a reload clears it. Persistence exists but is opt-in and off
 * until the user checks the box, because a key is a credential and the
 * difference between "this tab" and "this device" is one the person
 * typing it should make deliberately.
 *
 * Either way the key is never stored on the server. It travels on the
 * X-LLM-Key header of the requests that need it and nowhere else.
 */
export default function ApiKeyField({
  apiKey, onChange, remember, onRemember, onForget,
  labelClass, fieldClass, controlWidth,
}: Props) {
  const [visible, setVisible] = useState(false);
  const [test, setTest] = useState<Test>({ state: "idle" });
  // checked once: a browser that refuses storage must not offer the option
  const [canStore] = useState(storageAvailable);

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

      <div className="flex items-center justify-between gap-6">
        <span className={labelClass}>Remember this key on this device</span>
        <div className={`${controlWidth} shrink-0`}>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={remember}
              disabled={!canStore}
              onChange={(e) => onRemember(e.target.checked)}
              aria-describedby="api-key-storage-note"
              className="size-[14px] accent-bull disabled:cursor-not-allowed
                         disabled:opacity-40"
            />
            <span className="font-sans text-[13px] text-text">
              {canStore ? "Save in this browser" : "Storage unavailable"}
            </span>
          </label>
        </div>
      </div>

      <div className="flex items-start justify-between gap-6">
        <p
          id="api-key-storage-note"
          className="max-w-[220px] font-sans text-[12px] leading-snug text-label"
        >
          {remember ? (
            <>
              Saved in this browser. It survives closing the tab and is
              readable by anyone with access to this device. It is still
              never stored on the server.
            </>
          ) : (
            <>
              The key never leaves this tab — a reload clears it. It is sent
              only with your analysis requests, and never stored on the
              server.
            </>
          )}
        </p>
        <div className={`${controlWidth} shrink-0`}>
          <div className="flex gap-2">
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

          <button
            type="button"
            onClick={() => {
              onForget();
              setTest({ state: "idle" });
            }}
            disabled={!apiKey && !remember}
            title="Clear the field and remove any saved copy from this browser"
            className="h-[30px] rounded-md border border-ctl-border bg-ctl px-3 font-sans
                       text-[13px] font-medium text-ctl-text transition-colors
                       hover:border-bear hover:text-bear
                       disabled:cursor-not-allowed disabled:opacity-40"
          >
            Forget key
          </button>
          </div>

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
