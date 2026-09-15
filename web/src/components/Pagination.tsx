import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/**
 * Cursor pagination. The API returns an opaque `next_cursor`; earlier cursors
 * are remembered in memory for "Previous". A changed filter resets to page 1.
 */
export function useCursorPager(resetKey: string, cursor: string | null, setCursor: (cursor: string | null) => void) {
  const [history, setHistory] = useState<(string | null)[]>([]);
  const previousKey = useRef(resetKey);
  useEffect(() => {
    if (previousKey.current !== resetKey) {
      previousKey.current = resetKey;
      setHistory([]);
    }
  }, [resetKey]);
  return {
    page: history.length + 1,
    next: (nextCursor: string) => {
      setHistory((h) => [...h, cursor]);
      setCursor(nextCursor);
    },
    previous: () => {
      setHistory((h) => {
        const copy = [...h];
        const back = copy.pop() ?? null;
        setCursor(back);
        return copy;
      });
    },
    first: () => {
      setHistory([]);
      setCursor(null);
    },
    hasPrevious: history.length > 0 || cursor !== null,
  };
}

export function Pagination({
  page,
  hasPrevious,
  nextCursor,
  onPrevious,
  onNext,
  label,
}: {
  page: number;
  hasPrevious: boolean;
  nextCursor: string | null;
  onPrevious: () => void;
  onNext: (cursor: string) => void;
  label: string;
}) {
  if (!hasPrevious && !nextCursor) return null;
  return (
    <nav className="pagination" aria-label={`${label} pages`}>
      <button type="button" className="button button--secondary" onClick={onPrevious} disabled={!hasPrevious}>
        <ChevronLeft size={14} aria-hidden="true" /> Previous
      </button>
      <span className="pagination__page" aria-live="polite">
        Page {page}
      </span>
      <button type="button" className="button button--secondary" onClick={() => nextCursor && onNext(nextCursor)} disabled={!nextCursor}>
        Next <ChevronRight size={14} aria-hidden="true" />
      </button>
    </nav>
  );
}
