import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, X } from "lucide-react";
import ApiKeyField from "./ApiKeyField";
import { ZONES, ZONE_LABELS } from "../lib/timezone";
import type { Zone } from "../lib/timezone";

const TABS = ["Model", "General", "Notifications"] as const;
type Tab = (typeof TABS)[number];

/** Every control is the same width, so their left edges form one column. */
const CONTROL_W = "w-[200px]";

/** #0E1116 on the #1A2230 panel: fields read as inset, not raised. */
const FIELD =
  "h-[34px] rounded-md border border-ctl-border bg-base px-2.5 font-mono " +
  "text-[14px] text-text outline-none focus:border-bull";

/* Form labels are read, not scanned, so they use the sans UI face in
   sentence case. The mono/uppercase/wide-tracking treatment (.lbl) stays
   where it belongs: data labels on the chart and strips. */
const FIELD_LABEL = "font-sans text-[13px] font-medium text-ctl-text";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6">
      <span className={FIELD_LABEL}>{label}</span>
      {children}
    </div>
  );
}

function Field({ children }: { children: ReactNode }) {
  return <div className={`${CONTROL_W} shrink-0`}>{children}</div>;
}

/** The controlled sibling of SelectField, for a setting that actually applies. */
function ZoneField({ value, onChange }: { value: Zone; onChange: (z: Zone) => void }) {
  return (
    <Field>
      <div className="relative">
        <select
          value={value}
          aria-label="Display timezone"
          onChange={(e) => onChange(e.target.value as Zone)}
          className={`${FIELD} w-full appearance-none pr-8`}
        >
          {ZONES.map((z) => (
            <option key={z} value={z} className="bg-ctl text-text">
              {ZONE_LABELS[z]}
            </option>
          ))}
        </select>
        <ChevronDown
          size={14}
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-label"
        />
      </div>
    </Field>
  );
}

function TextField(props: { value: string; align?: "left" | "right" }) {
  return (
    <Field>
      <input
        defaultValue={props.value}
        className={`${FIELD} w-full ${props.align === "right" ? "text-right" : ""}`}
      />
    </Field>
  );
}

function SelectField({ value, options }: { value: string; options: string[] }) {
  return (
    <Field>
      {/* the native arrow does not match the terminal, so it is removed
          and a lucide chevron is placed inside the field instead */}
      <div className="relative">
        <select
          defaultValue={value}
          className={`${FIELD} w-full appearance-none pr-8`}
        >
          {options.map((o) => (
            <option key={o} value={o} className="bg-ctl text-text">
              {o}
            </option>
          ))}
        </select>
        <ChevronDown
          size={14}
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-label"
        />
      </div>
    </Field>
  );
}

function CheckField({ checked }: { checked?: boolean }) {
  // boxed to the control width so it lines up with the fields above it
  return (
    <Field>
      <input type="checkbox" defaultChecked={checked} className="h-4 w-4 accent-bull" />
    </Field>
  );
}

interface Props {
  onClose: () => void;
  /** Display zone for every rendered timestamp. Affects nothing stored. */
  zone: Zone;
  onZone: (zone: Zone) => void;
  /**
   * Held in the parent's React state, and additionally in this browser's
   * localStorage only when `remember` is on. Never sent to the server for
   * storage either way.
   */
  apiKey: string;
  onApiKey: (key: string) => void;
  remember: boolean;
  onRemember: (remember: boolean) => void;
  onForget: () => void;
}

export default function SettingsModal({
  onClose, apiKey, onApiKey, zone, onZone, remember, onRemember, onForget,
}: Props) {
  const [tab, setTab] = useState<Tab>("Model");
  const panel = useRef<HTMLDivElement>(null);

  // focus enters the modal on open and goes back to whatever opened it
  // (the gear) on close, so keyboard users are never dropped at the top
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(6,8,12,0.85)]
                 p-4 backdrop-blur-[4px]"
      onClick={onClose}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[480px] rounded-xl border border-ctl-border bg-ctl p-6
                   shadow-[0_16px_48px_rgba(0,0,0,0.6)] outline-none"
      >
        <div className="flex items-center justify-between">
          <h2 className="font-mono text-[12px] tracking-[0.14em] text-text uppercase">
            Settings
          </h2>
          <button
            onClick={onClose}
            aria-label="Close settings"
            className="inline-flex items-center gap-1.5 font-mono text-[13px] tracking-[0.12em]
                       text-label uppercase transition-colors hover:text-text"
          >
            <X size={14} />
            Close
          </button>
        </div>

        <div className="mt-5 flex gap-6 border-b border-ctl-border">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              aria-pressed={tab === t}
              className={`-mb-px border-b-2 pb-2.5 font-sans text-[13px] font-medium
                          transition-colors ${
                tab === t
                  ? "border-bull text-text"
                  : "border-transparent text-label hover:text-text"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="mt-5 flex flex-col gap-4">
          {tab === "Model" && (
            <>
              <Row label="Provider">
                <SelectField value="openai_compat" options={["mock", "openai_compat"]} />
              </Row>
              <Row label="Model">
                <TextField value="deepseek-chat" />
              </Row>
              <Row label="Temperature">
                <TextField value="0.20" align="right" />
              </Row>
              <Row label="Max tokens">
                <TextField value="2048" align="right" />
              </Row>

              <div className="mt-1 border-t border-ctl-border pt-4">
                <ApiKeyField
                  apiKey={apiKey}
                  onChange={onApiKey}
                  remember={remember}
                  onRemember={onRemember}
                  onForget={onForget}
                  labelClass={FIELD_LABEL}
                  fieldClass={FIELD}
                  controlWidth={CONTROL_W}
                />
              </div>
            </>
          )}

          {tab === "General" && (
            <>
              <Row label="Timezone">
                <ZoneField value={zone} onChange={onZone} />
              </Row>
              <p className="-mt-1 max-w-[320px] font-sans text-[12px] leading-snug text-label">
                Changes how times are displayed. Bars, analyses and scores are
                stored in UTC either way, and the model always reads UTC.
              </p>
              <Row label="Analyze every Nth bar">
                <TextField value="1" align="right" />
              </Row>
              <Row label="Risk per trade">
                <TextField value="100" align="right" />
              </Row>
              <Row label="Reset zoom on new data">
                <CheckField checked />
              </Row>
            </>
          )}

          {tab === "Notifications" && (
            <>
              <Row label="Alert on decision change">
                <CheckField checked />
              </Row>
              <Row label="Alert on stop breach">
                <CheckField checked />
              </Row>
              <Row label="Sound">
                <CheckField />
              </Row>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
