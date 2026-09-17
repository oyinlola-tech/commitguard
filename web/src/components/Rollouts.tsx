import { Link } from "react-router";

import type { PropagationStatus, Rollout } from "../api/types";
import { count, plural } from "../lib/format";
import { PROPAGATION, PROPAGATION_OPTIONS } from "../lib/labels";
import { routes } from "../lib/routes";
import { Badge } from "./Badge";
import { Notice, Time } from "./Primitives";

/** Enrollment is not completion: propagated and scan results are reported separately. */
export function RolloutProgress({ rollout }: { rollout: Rollout }) {
  return (
    <div className="rollout-progress">
      <progress className="progress" max={Math.max(rollout.scope_repositories, 1)} value={rollout.enrolled} aria-label={`${count(rollout.enrolled)} of ${count(rollout.scope_repositories)} repositories enrolled`} />
      <p className="small">
        Repositories {count(rollout.enrolled)} / {count(rollout.scope_repositories)} enrolled · {count(rollout.propagated)} propagated
      </p>
      <p className="muted small">
        {count(rollout.scanned)} scanned · {count(rollout.passed)} passed · {count(rollout.blocked)} blocked · {plural(rollout.errors, "error")}
      </p>
    </div>
  );
}

/**
 * Propagation counts as the server reports them. "Every repository resolves
 * the current policy" is shown only when the server says `complete`.
 */
export function Propagation({ propagation }: { propagation: PropagationStatus }) {
  const pendingStates = PROPAGATION_OPTIONS.filter((state) => state !== "up_to_date" && propagation[state] > 0);
  return (
    <>
      <progress className="progress" max={Math.max(propagation.repositories, 1)} value={propagation.up_to_date} aria-label={`${count(propagation.up_to_date)} of ${count(propagation.repositories)} repositories up to date`} />
      <p className="propagation__summary">
        <span className="strong">
          {count(propagation.up_to_date)} / {count(propagation.repositories)}
        </span>{" "}
        repositories up to date
        {pendingStates.map((state) => (
          <span key={state}>, {count(propagation[state])} {state === "error" ? "in error" : state === "pending" ? "never resolved" : state}</span>
        ))}
      </p>
      <ul className="badge-list">
        {PROPAGATION_OPTIONS.map((state) => (
          <li key={state} className="requirement">
            <Badge map={PROPAGATION} value={state} compact /> {count(propagation[state])}
          </li>
        ))}
      </ul>
      {propagation.complete ? (
        <Notice tone="success">Every repository resolves the current policy.</Notice>
      ) : (
        <p className="muted small">Not every repository has resolved the current policy yet. A scan never uses an out-of-date policy: it resolves the policy again when its cached copy is not current.</p>
      )}
      {propagation.failing.length ? (
        <Notice tone="danger" title="Repositories whose policy could not be resolved">
          <ul className="plain-list">
            {propagation.failing.map((f) => (
              <li key={f.repository_id}>
                <Link to={routes.repository(f.repository_id)}>{f.full_name}</Link>
                {f.error ? <span className="muted small"> · {f.error}</span> : null}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}
      <p className="muted small">
        Checked <Time value={propagation.checked_at} />
      </p>
    </>
  );
}

