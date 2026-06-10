import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { NAV_ITEMS, type PanelId } from "./nav";

export function Sidebar({
  activePanel,
  onNavigate,
}: {
  activePanel: PanelId;
  onNavigate: (id: PanelId) => void;
}) {
  const { t } = useI18n();
  return (
    <nav className="flex h-full w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
      <div className="px-4 py-4">
        <div className="text-sm font-semibold">{t("app.title")}</div>
        <div className="text-xs text-muted-foreground">{t("app.subtitle")}</div>
      </div>
      <div className="flex flex-col gap-1 px-2">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = item.id === activePanel;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              title={t(`nav.${item.id}.hint`)}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors",
                active
                  ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="truncate">{t(`nav.${item.id}`)}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
