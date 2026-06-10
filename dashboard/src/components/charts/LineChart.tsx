import { useId, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * Area line chart with gradient fill and hover tooltip.
 * Uses a fixed-height container so the SVG is never distorted.
 */
export function LineChart({
  labels,
  values,
  color = "#54b6ff",
  className,
}: {
  labels: string[];
  values: number[];
  color?: string;
  className?: string;
}) {
  const uid = useId();
  const gradId = `lc-grad-${uid}`;
  const clipId = `lc-clip-${uid}`;

  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  const W = 400;
  const H = 160;
  const padX = 8;
  const padTop = 14;
  const padBottom = 8;
  const chartH = H - padTop - padBottom;

  const n = values.length;
  const min = n ? Math.min(...values) : 0;
  const max = n ? Math.max(...values) : 1;
  // Give flat lines a small virtual range so they render at mid-height
  const span = Math.max(max - min, 1);
  const stepX = n > 1 ? (W - padX * 2) / (n - 1) : 0;

  const pts = values.map((v, i) => ({
    x: padX + i * stepX,
    y: padTop + chartH - ((v - min) / span) * chartH,
  }));

  const linePath = pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");

  // Area path: line down to baseline then back along bottom
  const areaPath =
    n > 0
      ? `${linePath} L${pts[n - 1].x.toFixed(1)},${H - padBottom} L${pts[0].x.toFixed(1)},${H - padBottom} Z`
      : "";

  const hov = hoveredIdx !== null ? pts[hoveredIdx] : null;
  const TW = 68;
  const TH = 36;

  return (
    <div className={cn("w-full", className)}>
      {/* Fixed-ratio wrapper: 400:160 = 2.5:1 */}
      <div className="relative w-full" style={{ paddingBottom: "40%" }}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="absolute inset-0 h-full w-full overflow-visible"
          onMouseLeave={() => setHoveredIdx(null)}
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.25} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
            <clipPath id={clipId}>
              <rect x={0} y={0} width={W} height={H} />
            </clipPath>
          </defs>

          {/* Horizontal grid lines */}
          {[0.25, 0.5, 0.75, 1].map((t) => {
            const gy = padTop + chartH * (1 - t);
            return (
              <line
                key={t}
                x1={padX}
                y1={gy.toFixed(1)}
                x2={W - padX}
                y2={gy.toFixed(1)}
                stroke="currentColor"
                strokeDasharray="3 5"
                strokeWidth={0.8}
                className="text-border"
              />
            );
          })}

          {/* Area fill */}
          {areaPath && (
            <path
              d={areaPath}
              fill={`url(#${gradId})`}
              clipPath={`url(#${clipId})`}
              className="pointer-events-none"
            />
          )}

          {/* Line */}
          <path
            d={linePath}
            fill="none"
            stroke={color}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            className="pointer-events-none"
          />

          {/* Vertical guide */}
          {hov && (
            <line
              x1={hov.x.toFixed(1)}
              y1={padTop}
              x2={hov.x.toFixed(1)}
              y2={H - padBottom}
              stroke={color}
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.45}
              className="pointer-events-none"
            />
          )}

          {/* Data points */}
          {pts.map((p, i) => (
            <g key={i}>
              <circle
                cx={p.x}
                cy={p.y}
                r={12}
                fill="transparent"
                className="cursor-crosshair"
                onMouseEnter={() => setHoveredIdx(i)}
              />
              {hoveredIdx === i && (
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={6}
                  fill={color}
                  opacity={0.2}
                  className="pointer-events-none"
                />
              )}
              <circle
                cx={p.x}
                cy={p.y}
                r={hoveredIdx === i ? 4 : 2.8}
                fill={hoveredIdx === i ? color : "var(--card)"}
                stroke={color}
                strokeWidth={1.8}
                className="pointer-events-none"
                style={{ transition: "r 0.1s" }}
              />
            </g>
          ))}

          {/* Tooltip */}
          {hov && hoveredIdx !== null && (() => {
            const tx = hov.x + TW + 6 > W ? hov.x - TW - 6 : hov.x + 6;
            const ty = hov.y - TH - 6 < 0 ? hov.y + 6 : hov.y - TH - 6;
            return (
              <g className="pointer-events-none">
                <rect
                  x={tx} y={ty}
                  width={TW} height={TH}
                  rx={5} ry={5}
                  fill="var(--popover)"
                  stroke="var(--border)"
                  strokeWidth={0.8}
                />
                <text
                  x={tx + TW / 2} y={ty + 13}
                  textAnchor="middle"
                  fontSize={9}
                  fill="var(--muted-foreground)"
                >
                  {labels[hoveredIdx]}
                </text>
                <text
                  x={tx + TW / 2} y={ty + 27}
                  textAnchor="middle"
                  fontSize={14}
                  fontWeight={700}
                  fill="var(--foreground)"
                >
                  {values[hoveredIdx]}
                </text>
              </g>
            );
          })()}
        </svg>
      </div>

      {/* X-axis labels */}
      <div className="mt-1 flex justify-between px-1 text-[10px] text-muted-foreground">
        {labels.map((l) => (
          <span key={l}>{l}</span>
        ))}
      </div>
    </div>
  );
}
