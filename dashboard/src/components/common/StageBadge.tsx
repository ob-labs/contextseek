import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Stability, Stage } from "@/lib/types";

const STAGE_STYLES: Record<Stage, string> = {
  raw: "bg-slate-200 text-slate-800",
  extracted: "bg-sky-200 text-sky-900",
  knowledge: "bg-emerald-200 text-emerald-900",
  skill: "bg-violet-200 text-violet-900",
};

const STABILITY_STYLES: Record<Stability, string> = {
  ephemeral: "bg-amber-100 text-amber-800",
  transient: "bg-orange-100 text-orange-800",
  stable: "bg-teal-100 text-teal-800",
  permanent: "bg-indigo-100 text-indigo-800",
};

export function StageBadge({ stage, className }: { stage: Stage; className?: string }) {
  return (
    <Badge variant="outline" className={cn("border-transparent", STAGE_STYLES[stage], className)}>
      {stage}
    </Badge>
  );
}

export function StabilityBadge({
  stability,
  className,
}: {
  stability: Stability;
  className?: string;
}) {
  return (
    <Badge
      variant="outline"
      className={cn("border-transparent", STABILITY_STYLES[stability], className)}
    >
      {stability}
    </Badge>
  );
}
