import { cn } from "@/lib/utils";

export interface DonutSegment {
  label: string;
  value: number;
  /** Any CSS color (hex / var / hsl). Drives both the ring slice and legend dot. */
  color: string;
}

/**
 * Conic-gradient donut with a side legend. Stateless — pass `segments` in.
 * Ported from the static mock's `donutChart()`.
 */
export function DonutChart({
  segments,
  className,
}: {
  segments: DonutSegment[];
  className?: string;
}) {
  const total = segments.reduce((s, seg) => s + seg.value, 0) || 1;

  let cursor = 0;
  const stops = segments
    .map((seg) => {
      const start = (cursor / total) * 360;
      cursor += seg.value;
      const end = (cursor / total) * 360;
      return `${seg.color} ${start.toFixed(1)}deg ${end.toFixed(1)}deg`;
    })
    .join(", ");

  return (
    <div className={cn("flex items-center gap-4", className)}>
      <div
        className="relative h-24 w-24 shrink-0 rounded-full"
        style={{ background: `conic-gradient(${stops})` }}
      >
        <div className="absolute inset-[18px] rounded-full border bg-card" />
      </div>
      <div className="flex flex-1 flex-col gap-1.5 text-xs">
        {segments.map((seg) => {
          const pct = Math.round((seg.value / total) * 100);
          return (
            <div key={seg.label} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2.5 w-2.5 rounded-sm"
                  style={{ background: seg.color }}
                />
                <span className="text-muted-foreground">{seg.label}</span>
              </span>
              <span className="tabular-nums text-foreground">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
