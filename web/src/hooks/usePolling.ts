import { useCallback, useRef } from "react";

export const POLL_INITIAL_MS = 2_000;
export const POLL_MAX_MS = 30_000;

/**
 * A `refetchInterval` for TanStack Query that polls while `active(data)` is
 * true, starting at 2 s and doubling up to 30 s, and stops as soon as the
 * server reports a final state. There is no endless one-second polling.
 */
export function useBackoffPolling<T>(active: (data: T | undefined) => boolean) {
  const attempts = useRef(0);
  return useCallback(
    (query: { state: { data: T | undefined } }) => {
      if (!active(query.state.data)) {
        attempts.current = 0;
        return false as const;
      }
      const delay = Math.min(POLL_MAX_MS, POLL_INITIAL_MS * 2 ** attempts.current);
      attempts.current += 1;
      return delay;
    },
    [active],
  );
}

export const isInProgress = (result: string | null | undefined): boolean => result === "queued" || result === "running";
