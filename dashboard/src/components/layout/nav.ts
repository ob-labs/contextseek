import {
  Eye,
  GitGraph,
  LayoutDashboard,
  PlusCircle,
  Plug,
  Search,
  Settings,
  Sparkles,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type PanelId =
  | "overview"
  | "retrieve"
  | "browse"
  | "write"
  | "evolution"
  | "ingress"
  | "provenance"
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
  { id: "browse", icon: Eye },
  { id: "write", icon: PlusCircle },
  { id: "evolution", icon: Sparkles },
  { id: "ingress", icon: Plug },
  { id: "provenance", icon: GitGraph },
  { id: "skills", icon: Zap },
  { id: "settings", icon: Settings },
];
