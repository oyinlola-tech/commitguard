import { Link } from "react-router";

import type { Finding } from "../api/types";
import { POLICY_ACTION, SEVERITY } from "../lib/labels";
import { routes } from "../lib/routes";
import { Badge } from "./Badge";
import { EvidenceList } from "./Evidence";
import { Sha } from "./Primitives";

export function FindingCard({ finding }: { finding: Finding }) {
  return (
    <article className={`finding finding--${finding.action}`} aria-labelledby={`finding-${finding.id}`}>
      <header className="finding__header">
        <h3 id={`finding-${finding.id}`} className="finding__title">
          {finding.title}
        </h3>
        <div className="finding__badges">
          <Badge map={SEVERITY} value={finding.severity} compact />
          <Badge map={POLICY_ACTION} value={finding.action} compact />
        </div>
      </header>
      <p>{finding.message}</p>
      <dl className="kv kv--inline">
        <div className="kv__row"><dt>Rule</dt><dd><Link to={routes.rule(finding.rule_id)}><code>{finding.rule_id}</code></Link></dd></div>
        <div className="kv__row"><dt>Commit</dt><dd><Sha value={finding.commit_sha} copy /></dd></div>
        <div className="kv__row"><dt>Author</dt><dd className="break">{finding.author ?? "—"}</dd></div>
        <div className="kv__row"><dt>Policy</dt><dd>{finding.reason}</dd></div>
        <div className="kv__row"><dt>Confidence</dt><dd>{finding.confidence}</dd></div>
      </dl>
      <EvidenceList evidence={finding.evidence} />
      <p className="finding__remediation">
        <span className="strong">Remediation: </span>
        {finding.remediation}
      </p>
      {finding.violation_id ? (
        <p className="small">
          <Link to={routes.violation(finding.violation_id)}>Open violation history</Link>
        </p>
      ) : null}
    </article>
  );
}
