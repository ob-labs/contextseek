import { type ClassValue, clsx } from "clsx";
import { useCallback, useState } from "react";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: unknown;
  run: (...args: unknown[]) => Promise<T | undefined>;
  reset: () => void;
}

/**
 * Minimal async runner for one-shot API calls.
 * `fn` is invoked with whatever args you pass to `run`.
 */
export function useAsyncFn<T>(fn: (...args: never[]) => Promise<T>): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = useCallback(
    async (...args: unknown[]) => {
      setLoading(true);
      setError(null);
      try {
        const result = await fn(...(args as never[]));
        setData(result);
        return result;
      } catch (err) {
        setError(err);
        return undefined;
      } finally {
        setLoading(false);
      }
    },
    [fn],
  );

  const reset = useCallback(() => {
    setData(null);
    setError(null);
    setLoading(false);
  }, []);

  return { data, loading, error, run, reset };
}

export function errorMessage(err: unknown): string {
  if (!err) return "";
  if (err instanceof Error) {
    const body = (err as { body?: unknown }).body;
    if (body && typeof body === "object" && "detail" in body) {
      return String((body as { detail: unknown }).detail);
    }
    if (typeof body === "string" && body) return body;
    return err.message;
  }
  return String(err);
}
