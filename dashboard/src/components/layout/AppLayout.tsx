import type { PanelId } from "./nav";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppLayout({
  activePanel,
  onNavigate,
  children,
}: {
  activePanel: PanelId;
  onNavigate: (id: PanelId) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-full w-full">
      <Sidebar activePanel={activePanel} onNavigate={onNavigate} />
      <div className="flex h-full min-w-0 flex-1 flex-col">
        <Topbar activePanel={activePanel} />
        <main className="min-h-0 flex-1 overflow-auto">{children}</main>
      </div>
    </div>
  );
}
