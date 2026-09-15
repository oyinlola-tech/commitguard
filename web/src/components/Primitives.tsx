import { Check, Copy, ExternalLink as ExternalIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { dateTime, relativeTime, shortSha } from "../lib/format";
import { isGitHubUrl } from "../lib/routes";

export function PageHeader({
  title,
  eyebrow,
  description,
  actions,
  children,
}: {
  title: ReactNode;
  eyebrow?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        {eyebrow ? <p className="page-header__eyebrow">{eyebrow}</p> : null}
        <h1 className="page-header__title">{title}</h1>
        {description ? <p className="page-header__description">{description}</p> : null}
        {children}
      </div>
      {actions ? <div className="page-header__actions">{actions}</div> : null}
    </header>
  );
}

export function Panel({
  title,
  actions,
  children,
  id,
  tone,
  flush = false,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
  tone?: "danger" | "warning";
  flush?: boolean;
}) {
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className={`panel${tone ? ` panel--${tone}` : ""}`} aria-labelledby={title ? headingId : undefined} id={id}>
      {title ? (
        <div className="panel__header">
          <h2 className="panel__title" id={headingId}>
            {title}
          </h2>
          {actions ? <div className="panel__actions">{actions}</div> : null}
        </div>
      ) : null}
      <div className={flush ? "panel__body panel__body--flush" : "panel__body"}>{children}</div>
    </section>
  );
}

export function KeyValueList({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([key, value], index) => (
        <div className="kv__row" key={index}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Time({ value, absolute = false }: { value: string | null | undefined; absolute?: boolean }) {
  if (!value) return <span className="muted">Never</span>;
  return (
    <time dateTime={value} title={dateTime(value)}>
      {absolute ? dateTime(value) : relativeTime(value)}
    </time>
  );
}

export function Sha({ value, copy = false, length = 7 }: { value: string | null | undefined; copy?: boolean; length?: number }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="muted">—</span>;
  const copyValue = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <span className="sha">
      <code title={value}>{shortSha(value, length)}</code>
      {copy ? (
        <button type="button" className="icon-button icon-button--small" onClick={() => void copyValue()} aria-label={copied ? "Copied" : `Copy full SHA ${value}`}>
          {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
        </button>
      ) : null}
    </span>
  );
}

export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  if (!isGitHubUrl(href)) return <>{children}</>;
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className="external-link">
      {children}
      <ExternalIcon size={12} aria-hidden="true" />
      <span className="visually-hidden"> (opens GitHub in a new tab)</span>
    </a>
  );
}

export function Metric({
  label,
  value,
  detail,
  to,
  tone,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  to?: string;
  tone?: "danger" | "critical";
}) {
  const content = (
    <>
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
      {detail ? <span className="metric__detail">{detail}</span> : null}
    </>
  );
  const className = `metric${tone ? ` metric--${tone}` : ""}`;
  return to ? (
    <Link className={`${className} metric--link`} to={to}>
      {content}
    </Link>
  ) : (
    <div className={className}>{content}</div>
  );
}

export function Notice({ tone = "info", title, children }: { tone?: "info" | "warning" | "danger" | "success"; title?: ReactNode; children: ReactNode }) {
  return (
    <div className={`notice notice--${tone}`} role={tone === "danger" ? "alert" : undefined}>
      {title ? <p className="notice__title">{title}</p> : null}
      <div className="notice__body">{children}</div>
    </div>
  );
}
