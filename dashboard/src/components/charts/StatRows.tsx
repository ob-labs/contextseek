import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface StatRow {
  label: ReactNode;
  value: ReactNode;
}

/**
 * label / value rows with dashed separators. Ported from the mock's `rows()`.
 * Pass `highlightFirst` to emphasize the first row (mock's `row-main`).
 */
export function StatRows({
  rows,
  highlightFirst = false,
  className,
}: {
  rows: StatRow[];
  highlightFirst?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("text-xs", className)}>
      {rows.map((row, i) => (
        <div
          key={i}
          className={cn(
            "flex items-center justify-between gap-2 py-2",
            i > 0 && "border-t border-dashed border-border",
            highlightFirst && i === 0 ? "text-foreground" : "text-muted-foreground",
          )}
        >
          <span>{row.label}</span>
          <span className="text-right">{row.value}</span>
        </div>
      ))}
    </div>
  );
}
