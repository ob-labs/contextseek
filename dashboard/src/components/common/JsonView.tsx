import { cn } from "@/lib/utils";

/** Pretty-print arbitrary content: strings as-is, objects as indented JSON. */
export function JsonView({ value, className }: { value: unknown; className?: string }) {
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? String(value);
  return (
    <pre
      className={cn(
        "max-h-80 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap break-words",
        className,
      )}
    >
      {text}
    </pre>
  );
}
