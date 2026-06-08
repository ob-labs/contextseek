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
import { useScope } from "@/context/ScopeContext";
import { errorMessage } from "@/lib/utils";

/**
 * Inline lifecycle actions for a single item: feedback / forget / delete.
 * `onChanged` lets the parent refresh its list afterwards.
 */
export function ItemActions({ itemId, onChanged }: { itemId: string; onChanged?: () => void }) {
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
        👍 有用
      </AsyncButton>
      <AsyncButton
        size="sm"
        variant="outline"
        loading={busy === "feedback-"}
        onClick={() => act("feedback-", () => ctx.feedback({ scope, item_id: itemId, score: -1 }))}
      >
        👎 没用
      </AsyncButton>
      <AsyncButton
        size="sm"
        variant="outline"
        loading={busy === "forget"}
        onClick={() => act("forget", () => ctx.forget({ scope, item_id: itemId }))}
      >
        忘记 (软删)
      </AsyncButton>
      <Button size="sm" variant="destructive" onClick={() => setConfirmDelete(true)}>
        <Trash2 className="h-4 w-4" /> 删除
      </Button>
      {msg && <span className="text-xs text-muted-foreground">{msg}</span>}

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认硬删除？</DialogTitle>
            <DialogDescription>
              将永久删除条目 <span className="font-mono">{itemId}</span>，不可恢复。
            </DialogDescription>
          </DialogHeader>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={propagate}
              onChange={(e) => setPropagate(e.target.checked)}
            />
            级联删除派生条目 (propagate)
          </label>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              取消
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
              确认删除
            </AsyncButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
