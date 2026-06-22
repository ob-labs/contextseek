import { CircleHelp } from "lucide-react";

import { cn } from "@/lib/utils";

export function HelpHint({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <span className={cn("group relative inline-flex", className)}>
      <span
        className="inline-flex h-4 w-4 items-center justify-center text-muted-foreground"
        aria-label="help"
      >
        <CircleHelp className="h-4 w-4" />
      </span>
      <span className="pointer-events-none absolute right-0 top-full z-50 mt-1 hidden w-72 rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md group-hover:block group-focus-within:block">
        {content}
      </span>
    </span>
  );
}
