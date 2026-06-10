import { Trash2 } from "lucide-react";
import { useState } from "react";

import { AsyncButton } from "@/components/common/AsyncButton";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ctx } from "@/lib/ctxClient";
import { useI18n } from "@/lib/i18n";
import { useScope } from "@/context/ScopeContext";
import { errorMessage } from "@/lib/utils";

/**
 * Inline lifecycle actions for a single item: feedback / forget / delete.
 * `onChanged` lets the parent refresh its list afterwards.
 */
export function ItemActions({ itemId, onChanged }: { itemId: string; onChanged?: () => void }) {
  const { t } = useI18n();
  const { scope } = useScope();
  const [busy, setBusy] = useState<string>("");
  const [msg, setMsg] = useState<string>("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [propagate, setPropagate] = useState(true);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    setMsg("");
    try {
      await fn();
      setMsg(`${name} ✓`);
      onChanged?.();
    } catch (err) {
      setMsg(errorMessage(err));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <AsyncButton
        size="sm"
        variant="outline"
        loading={busy === "feedback+"}
        onClick={() => act("feedback+", () => ctx.feedback({ scope, item_id: itemId, score: 1 }))}
      >
        {t("item.useful")}
      </AsyncButton>
      <AsyncButton
        size="sm"
        variant="outline"
        loading={busy === "feedback-"}
        onClick={() => act("feedback-", () => ctx.feedback({ scope, item_id: itemId, score: -1 }))}
      >
        {t("item.useless")}
      </AsyncButton>
      <AsyncButton
        size="sm"
        variant="outline"
        loading={busy === "forget"}
        onClick={() => act("forget", () => ctx.forget({ scope, item_id: itemId }))}
      >
        {t("item.forget")}
      </AsyncButton>
      <Button size="sm" variant="destructive" onClick={() => setConfirmDelete(true)}>
        <Trash2 className="h-4 w-4" /> {t("item.delete")}
      </Button>
      {msg && <span className="text-xs text-muted-foreground">{msg}</span>}

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("item.confirmDeleteTitle")}</DialogTitle>
            <DialogDescription>
              {t("item.confirmDeleteDesc", { id: itemId })}
            </DialogDescription>
          </DialogHeader>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={propagate}
              onChange={(e) => setPropagate(e.target.checked)}
            />
            {t("item.propagate")}
          </label>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              {t("common.cancel")}
            </Button>
            <AsyncButton
              variant="destructive"
              loading={busy === "delete"}
              onClick={async () => {
                await act("delete", () =>
                  ctx.delete({ scope, item_id: itemId, propagate }),
                );
                setConfirmDelete(false);
              }}
            >
              {t("item.confirmDelete")}
            </AsyncButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
