import { statusStyle, type StatusStyle } from "../lib/labels";

/** A status shown as icon + text; colour is never the only signal. */
export function Badge({
  map,
  value,
  compact = false,
  title,
}: {
  map: Record<string, StatusStyle>;
  value: string | null | undefined;
  compact?: boolean;
  title?: string;
}) {
  const style = statusStyle(map, value);
  const Icon = style.icon;
  const spinning = value === "running";
  return (
    <span className={`badge badge--${style.tone}${compact ? " badge--compact" : ""}`} title={title}>
      <Icon size={compact ? 12 : 14} aria-hidden="true" className={spinning ? "spin" : undefined} />
      <span>{style.label}</span>
    </span>
  );
}
