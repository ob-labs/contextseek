import { cn } from "@/lib/utils";

/** A small horizontal bar for a 0..1 score / confidence value. */
export function ConfidenceBar({
  value,
  label,
  className,
}: {
  value: number;
  label?: string;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color = pct >= 66 ? "bg-emerald-500" : pct >= 33 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className={cn("flex items-center gap-2", className)}>
      {label && <span className="text-xs text-muted-foreground">{label}</span>}
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs tabular-nums text-muted-foreground">
        {value.toFixed(2)}
      </span>
    </div>
  );
}
