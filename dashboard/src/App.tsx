import { useCallback, useState } from "react";

import { AppLayout } from "@/components/layout/AppLayout";
import type { PanelId } from "@/components/layout/nav";
import { NavContext, type NavTarget } from "@/context/NavContext";
import { ScopeProvider } from "@/context/ScopeContext";
import { BrowsePanel } from "@/panels/BrowsePanel";
import { EvolutionPanel } from "@/panels/EvolutionPanel";
import { ProvenancePanel } from "@/panels/ProvenancePanel";
import { RetrievePanel } from "@/panels/RetrievePanel";
import { WritePanel } from "@/panels/WritePanel";

export function App() {
  const [panel, setPanel] = useState<PanelId>("retrieve");
  const [prevPanel, setPrevPanel] = useState<PanelId | null>(null);
  const [provenanceItemId, setProvenanceItemId] = useState<string>("");

  const navigate = useCallback((next: PanelId, target?: NavTarget) => {
    if (target?.itemId !== undefined) setProvenanceItemId(target.itemId);
    setPrevPanel((cur) => (cur !== next ? panel : cur));
    setPanel(next);
  }, [panel]);

  const back = useCallback(() => {
    if (prevPanel) {
      setPanel(prevPanel);
      setPrevPanel(null);
    }
  }, [prevPanel]);

  return (
    <ScopeProvider>
      <NavContext.Provider value={{ navigate, back, canGoBack: prevPanel !== null }}>
        <div className="app-root">
          <AppLayout activePanel={panel} onNavigate={setPanel}>
            {panel === "retrieve" && <RetrievePanel />}
            {panel === "browse" && <BrowsePanel />}
            {panel === "write" && <WritePanel />}
            {panel === "evolution" && <EvolutionPanel />}
            {panel === "provenance" && <ProvenancePanel initialItemId={provenanceItemId} />}
          </AppLayout>
        </div>
      </NavContext.Provider>
    </ScopeProvider>
  );
}
