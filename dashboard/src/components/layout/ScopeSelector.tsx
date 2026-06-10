import { useEffect, useState } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { ctx } from "@/lib/ctxClient";
import { useScope } from "@/context/ScopeContext";

export function ScopeSelector() {
  const { scope, setScope } = useScope();
  const [options, setOptions] = useState<string[]>([]);

  useEffect(() => {
    ctx
      .scopes()
      .then((r) => setOptions(r.scopes))
      .catch(() => {});
  }, []);

  // Ensure current scope is always in the list (even if not returned by /scopes yet)
  const allOptions = options.includes(scope) ? options : [scope, ...options];

  return (
    <div className="flex items-center gap-2">
      <Label htmlFor="ctx-scope" className="shrink-0 text-xs text-muted-foreground">
        scope
      </Label>
      <Select value={scope} onValueChange={setScope}>
        <SelectTrigger id="ctx-scope" className="h-8 w-44 font-mono text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {allOptions.map((s) => (
            <SelectItem key={s} value={s} className="font-mono text-xs">
              {s}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
