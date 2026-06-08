import { createContext, useContext } from "react";

import type { PanelId } from "@/components/layout/nav";

export interface NavTarget {
  /** Pre-fill an item id when jumping to the provenance panel. */
  itemId?: string;
}

interface NavContextValue {
  navigate: (panel: PanelId, target?: NavTarget) => void;
  /** Navigate back to the panel that was active before the last navigate() call. */
  back: () => void;
  /** Whether there is a previous panel to go back to. */
  canGoBack: boolean;
}

export const NavContext = createContext<NavContextValue | null>(null);

export function useNav() {
  const ctx = useContext(NavContext);
  if (!ctx) throw new Error("useNav must be used within NavContext.Provider");
  return ctx;
}
