/** The conversation id sent with every question, kept so history survives a reload. */

const KEY = "frh.session-id";

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `s-${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

export function loadSessionId(): string {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored) return stored;
    const created = newId();
    localStorage.setItem(KEY, created);
    return created;
  } catch {
    return newId(); // private mode or blocked storage: this conversation just will not outlive the tab
  }
}

export function resetSessionId(): string {
  const created = newId();
  try {
    localStorage.setItem(KEY, created);
  } catch {
    // Nothing to persist to; the new id still drives this tab.
  }
  return created;
}

export function storeSessionId(sessionId: string): string {
  try {
    localStorage.setItem(KEY, sessionId);
  } catch {
    // The chosen conversation remains available for this tab even without storage access.
  }
  return sessionId;
}
