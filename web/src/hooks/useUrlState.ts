import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router";

/**
 * Filters and cursors live in the URL, so every list view is deep-linkable and
 * survives a refresh. Values are passed to the API as query parameters, where
 * the server validates them; nothing here builds SQL or HTML.
 */
export function useUrlState<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams();
  const values = useMemo(() => {
    const result = {} as Record<K, string>;
    for (const key of keys) result[key] = params.get(key) ?? "";
    return result;
  }, [params, keys]);

  const update = useCallback(
    (changes: Partial<Record<K | "cursor", string | null>>, { resetCursor = true } = {}) => {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [key, value] of Object.entries(changes) as [string, string | null][]) {
            if (value === null || value === "") next.delete(key);
            else next.set(key, value);
          }
          if (resetCursor && !("cursor" in changes)) next.delete("cursor");
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return { values, cursor: params.get("cursor"), update };
}
