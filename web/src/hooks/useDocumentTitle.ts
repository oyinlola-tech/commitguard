import { useEffect } from "react";

export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} · CommitGuard` : "CommitGuard";
  }, [title]);
}
