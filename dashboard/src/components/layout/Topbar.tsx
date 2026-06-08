import { NAV_ITEMS, type PanelId } from "./nav";
import { HealthBadge } from "./HealthBadge";
import { ScopeSelector } from "./ScopeSelector";

export function Topbar({ activePanel }: { activePanel: PanelId }) {
  const item = NAV_ITEMS.find((i) => i.id === activePanel);
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b px-6">
      <div>
        <div className="text-sm font-semibold">{item?.label}</div>
        <div className="text-xs text-muted-foreground">{item?.hint}</div>
      </div>
      <div className="flex items-center gap-4">
        <HealthBadge />
        <ScopeSelector />
      </div>
    </header>
  );
}
