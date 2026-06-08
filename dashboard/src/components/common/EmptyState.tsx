import { Loader2 } from "lucide-react";

import { errorMessage } from "@/lib/utils";

/** Unified loading / error / empty display for panels. */
export function EmptyState({
  loading,
  error,
  empty,
  emptyText = "暂无数据",
  children,
}: {
  loading?: boolean;
  error?: unknown;
  empty?: boolean;
  emptyText?: string;
  children?: React.ReactNode;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
        {errorMessage(error)}
      </div>
    );
  }
  if (empty) {
    return <div className="p-6 text-sm text-muted-foreground">{emptyText}</div>;
  }
  return <>{children}</>;
}
