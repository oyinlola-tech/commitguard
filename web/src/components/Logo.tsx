/**
 * The CommitGuard mark: a commit node on its branch line, held between
 * inspection brackets - a commit passing a checkpoint.
 */
export function LogoMark({ size = 22, title }: { size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" className="logo-mark" role={title ? "img" : undefined} aria-hidden={title ? undefined : true} aria-label={title}>
      <path d="M16 2.5v8M16 21.5v8" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="16" cy="16" r="5" fill="none" stroke="currentColor" strokeWidth="2.6" />
      <path d="M8.5 7.5H4.5v17h4M23.5 7.5h4v17h-4" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="square" strokeLinejoin="miter" />
    </svg>
  );
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <span className="logo">
      <LogoMark size={size} />
      <span className="logo__word">
        Commit<span className="logo__guard">Guard</span>
      </span>
    </span>
  );
}
