import {
  GitGraph,
  LayoutDashboard,
  Plug,
  PlusCircle,
  Search,
  Settings,
  Sparkles,
  Table2,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type PanelId =
  | "overview"
  | "retrieve"
  | "browse"
  | "write"
  | "evolution"
  | "provenance"
  | "ingress"
  | "skills"
  | "settings";

// Labels and hints are resolved via i18n at render time using the `id`
// (keys `nav.<id>` and `nav.<id>.hint` in lib/i18n.tsx).
export interface NavItem {
  id: PanelId;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "overview", icon: LayoutDashboard },
  { id: "retrieve", icon: Search },
  { id: "browse", icon: Table2 },
  { id: "write", icon: PlusCircle },
  { id: "evolution", icon: Sparkles },
  { id: "provenance", icon: GitGraph },
  { id: "ingress", icon: Plug },
  { id: "skills", icon: Zap },
  { id: "settings", icon: Settings },
];
