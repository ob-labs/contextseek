import { Loader2 } from "lucide-react";

import { Button, type ButtonProps } from "@/components/ui/button";

/** Button that shows a spinner and disables while `loading`. */
export function AsyncButton({
  loading,
  children,
  disabled,
  ...props
}: ButtonProps & { loading?: boolean }) {
  return (
    <Button disabled={loading || disabled} {...props}>
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </Button>
  );
}
