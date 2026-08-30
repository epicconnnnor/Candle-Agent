/**
 * Opt-in persistence for a visitor's own LLM key.
 *
 * The key is a credential, so persistence is off unless the user asks for
 * it. What changes when they do is genuinely different in kind, not
 * degree: unchecked, the key exists only in React state and a reload
 * loses it; checked, it is written to this browser's localStorage, where
 * it survives restarts and is readable by anything with access to this
 * origin on this device. The UI says exactly that rather than calling it
 * "remembered".
 *
 * What does NOT change: the key is never sent to the server for storage.
 * It rides the X-LLM-Key header on the requests that need it and nothing
 * else, and there is deliberately no server-side equivalent of this.
 *
 * Every access is wrapped. localStorage throws outright in some contexts
 * - Safari private browsing, embedded webviews, a browser set to block
 * site data - and a settings panel that cannot render because storage is
 * unavailable would be a worse failure than not persisting.
 */
const STORAGE_KEY = "candle-agent.llm-key";

export function loadStoredKey(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function storeKey(key: string): void {
  try {
    if (key) window.localStorage.setItem(STORAGE_KEY, key);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: the key still works for this session */
  }
}

export function forgetStoredKey(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do - it was never written */
  }
}

/** Whether persistence is actually available, so the UI can say so. */
export function storageAvailable(): boolean {
  try {
    const probe = `${STORAGE_KEY}.probe`;
    window.localStorage.setItem(probe, "1");
    window.localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}
