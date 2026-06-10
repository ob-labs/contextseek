import { useI18n } from "@/lib/i18n";
import { NAV_ITEMS, type PanelId } from "./nav";
import { HealthBadge } from "./HealthBadge";
import { ScopeSelector } from "./ScopeSelector";
import { TopbarControls } from "./TopbarControls";

export function Topbar({ activePanel }: { activePanel: PanelId }) {
  const { t } = useI18n();
  const item = NAV_ITEMS.find((i) => i.id === activePanel);
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b px-6">
      <div>
        <div className="text-sm font-semibold">{item ? t(`nav.${item.id}`) : ""}</div>
        <div className="text-xs text-muted-foreground">{item ? t(`nav.${item.id}.hint`) : ""}</div>
      </div>
      <div className="flex items-center gap-4">
        <HealthBadge />
        <ScopeSelector />
        <TopbarControls />
      </div>
    </header>
  );
}
