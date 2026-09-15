export function LogoMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo-mark">
      <path d="M16 3.5 6 7.4v7.4c0 6.4 4.2 11.7 10 13.2 5.8-1.5 10-6.8 10-13.2V7.4L16 3.5Z" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" />
      <circle cx="16" cy="15.5" r="3" fill="currentColor" />
      <path d="M16 8.5v4M16 18.5v4.5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <LogoMark />
      <span className="logo__word">CommitGuard</span>
    </span>
  );
}
