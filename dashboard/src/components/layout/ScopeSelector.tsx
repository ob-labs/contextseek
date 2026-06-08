import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useScope } from "@/context/ScopeContext";

/** The only writer of the global scope. Commits on blur / Enter. */
export function ScopeSelector() {
  const { scope, setScope } = useScope();
  const [draft, setDraft] = useState(scope);

  useEffect(() => setDraft(scope), [scope]);

  const commit = () => {
    const next = draft.trim();
    if (next && next !== scope) setScope(next);
    else setDraft(scope);
  };

  return (
    <div className="flex items-center gap-2">
      <Label htmlFor="ctx-scope" className="text-xs text-muted-foreground">
        scope
      </Label>
      <Input
        id="ctx-scope"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
        className="h-8 w-44 font-mono text-xs"
        spellCheck={false}
      />
    </div>
  );
}
