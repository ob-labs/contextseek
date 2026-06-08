import {
  GitGraph,
  PlusCircle,
  Search,
  Sparkles,
  Table2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type PanelId =
  | "retrieve"
  | "browse"
  | "write"
  | "evolution"
  | "provenance";

export interface NavItem {
  id: PanelId;
  label: string;
  icon: LucideIcon;
  hint: string;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "retrieve", label: "检索", icon: Search, hint: "语义检索 + 展开全文" },
  { id: "browse", label: "浏览", icon: Table2, hint: "按 scope / 阶段浏览记忆" },
  { id: "write", label: "写入", icon: PlusCircle, hint: "写入一条上下文" },
  { id: "evolution", label: "演化 / 生命周期", icon: Sparkles, hint: "compact / dream / 反馈 / 删除" },
  { id: "provenance", label: "溯源图谱", icon: GitGraph, hint: "证据链 DAG 与派生回溯" },
];
