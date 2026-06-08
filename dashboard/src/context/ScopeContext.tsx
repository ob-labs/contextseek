import { createContext, useCallback, useContext, useEffect, useState } from "react";

const STORAGE_KEY = "ctx.scope";
// Initial scope on first open (before the user picks one — the choice is then
// persisted to localStorage and editable from the UI). "contextseek" is the
// scope that seed.py pre-loads example knowledge into, so the Browse panel
// shows data on first open.
const DEFAULT_SCOPE = "contextseek";

interface ScopeContextValue {
  scope: string;
  setScope: (scope: string) => void;
}

const ScopeContext = createContext<ScopeContextValue | null>(null);

export function ScopeProvider({ children }: { children: React.ReactNode }) {
  const [scope, setScopeState] = useState<string>(() => {
    if (typeof window !== "undefined") {
      return window.localStorage.getItem(STORAGE_KEY) || DEFAULT_SCOPE;
    }
    return DEFAULT_SCOPE;
  });

  useEffect(() => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, scope);
    }
  }, [scope]);

  const setScope = useCallback((next: string) => setScopeState(next), []);

  return <ScopeContext.Provider value={{ scope, setScope }}>{children}</ScopeContext.Provider>;
}

export function useScope() {
  const ctx = useContext(ScopeContext);
  if (!ctx) throw new Error("useScope must be used within a ScopeProvider");
  return ctx;
}
