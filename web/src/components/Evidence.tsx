import type { Evidence } from "../api/types";

/** Evidence values are untrusted commit metadata; they are rendered as text only. */
export function EvidenceList({ evidence, heading = true }: { evidence: Evidence[]; heading?: boolean }) {
  if (evidence.length === 0) return null;
  return (
    <div className="evidence">
      {heading ? <p className="evidence__heading">Evidence</p> : null}
      <ul>
        {evidence.map((item, index) => (
          <li key={index} className="evidence__item">
            <span className="evidence__source">
              {item.source_label}
              {item.line_number ? ` · line ${item.line_number}` : ""}
            </span>
            <code className="evidence__value">{item.value}</code>
            {item.matched.length ? (
              <span className="evidence__matched">
                Matched {item.matched.map((m) => `${m.kind.replace(/_/g, " ")} "${m.value}"`).join(", ")}
              </span>
            ) : null}
            {item.notes.length ? <span className="evidence__notes">{item.notes.join("; ")}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
