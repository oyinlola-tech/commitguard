const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const DATE_TIME = new Intl.DateTimeFormat("en", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short",
});

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "Never";
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return RELATIVE.format(Math.round(seconds / size), unit);
  }
  return Math.abs(seconds) < 30 ? "just now" : RELATIVE.format(seconds, "second");
}

export function dateTime(iso: string | null | undefined): string {
  return iso ? DATE_TIME.format(new Date(iso)) : "—";
}

export function shortSha(sha: string | null | undefined, length = 7): string {
  return sha ? sha.slice(0, length) : "—";
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds % 60)} s`;
}

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : new Intl.NumberFormat("en").format(value);
}

export function plural(value: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count(value)} ${value === 1 ? singular : pluralForm}`;
}

export function scanEvent(scan: { event: string; pull_request_number: number | null; ref: string | null }): string {
  if (scan.event === "merge_group") return `Merge queue for ${scan.ref?.replace(/^refs\/heads\//, "") ?? "branch"}`;
  if (scan.pull_request_number !== null) return `Pull request #${scan.pull_request_number}`;
  if (scan.event === "push") return `Push to ${scan.ref?.replace(/^refs\/heads\//, "") ?? "branch"}`;
  return scan.event;
}

export function humanize(identifier: string): string {
  return identifier.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
