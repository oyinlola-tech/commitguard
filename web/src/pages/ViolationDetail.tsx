import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, GitBranch, GitPullRequest } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { acknowledgeViolation, getViolation, removeAcknowledgement } from "../api/violations";
import { Badge } from "../components/Badge";
import { EvidenceList } from "../components/Evidence";
import { KeyValueList, Notice, PageHeader, Panel, Sha, Time } from "../components/Primitives";
import { ErrorState, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { POLICY_ACTION, SCAN_RESULT, SEVERITY, VIOLATION_STATUS } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

const HEX_ID = /^[0-9a-f]{32}$/;

export default function ViolationDetail() {
  const { violationId = "" } = useParams();
  const valid = HEX_ID.test(violationId);
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["violation", violationId], queryFn: () => getViolation(violationId), enabled: valid });
  useDocumentTitle(query.data?.violation.title ?? "Violation");
  const [note, setNote] = useState("");
  const onSaved = (data: unknown) => {
    queryClient.setQueryData(["violation", violationId], data);
    void queryClient.invalidateQueries({ queryKey: ["violations"] });
    setNote("");
  };
  const acknowledge = useMutation({ mutationFn: () => acknowledgeViolation(violationId, note.trim() || null), onSuccess: (r) => onSaved(r.data) });
  const unacknowledge = useMutation({ mutationFn: () => removeAcknowledgement(violationId), onSuccess: (r) => onSaved(r.data) });

  if (!valid) return <NotFoundContent resource="violation" />;
  if (query.isPending) return <SkeletonRows rows={8} label="Loading violation…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="violation" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this violation." error={query.error} onRetry={() => void query.refetch()} />;

  const detail = query.data;
  const v = detail.violation;
  const active = detail.exposures.filter((e) => e.active);
  const mutationError = acknowledge.error ?? unacknowledge.error;
  return (
    <>
      <PageHeader eyebrow={<Link to={routes.violations}>Violations</Link>} title={v.title} description={<span>{detail.message}</span>}>
        <div className="badge-row">
          <Badge map={SEVERITY} value={v.severity} />
          <Badge map={POLICY_ACTION} value={v.action} />
          <Badge map={VIOLATION_STATUS} value={v.status} />
        </div>
      </PageHeader>

      {v.status === "resolved" ? (
        <Notice tone="success" title={<><CircleCheck size={16} aria-hidden="true" /> No longer present</>}>
          Resolved <Time value={detail.resolved_at} />: {detail.resolution}. The detection history below remains for audit.
        </Notice>
      ) : (
        <Notice tone="danger" title="Currently present">
          Detected in {active.map((e) => (e.kind === "pull_request" ? `pull request ${e.label}` : e.label)).join(", ") || "a monitored branch"}. It resolves automatically when CommitGuard scans a version without this commit.
        </Notice>
      )}

      <div className="grid-2">
        <Panel title="Details" id="violation-details">
          <KeyValueList
            items={[
              ["Rule", <Link to={routes.rule(v.rule_id)}><code>{v.rule_id}</code></Link>],
              ["Severity", <Badge map={SEVERITY} value={v.severity} compact />],
              ["Policy action", <Badge map={POLICY_ACTION} value={v.action} compact />],
              ["Policy", detail.policy_reason || "—"],
              ["Repository", <Link to={routes.repository(v.repository.id)}>{v.repository.full_name}</Link>],
              ["Commit SHA", <Sha value={v.commit_sha} copy length={12} />],
              ["Author", <span className="break">{v.author ?? "—"}</span>],
              ["Committer", <span className="break">{detail.committer ?? "—"}</span>],
              ["Detection source", detail.detector],
              ["First detected", <Time value={v.first_detected_at} absolute />],
              ["Last detected", <Time value={v.last_detected_at} absolute />],
              ["Current status", <Badge map={VIOLATION_STATUS} value={v.status} compact />],
            ]}
          />
        </Panel>
        <Panel title="Evidence" id="evidence">
          <EvidenceList evidence={detail.evidence} heading={false} />
          <p className="muted small">Only the commit metadata that triggered the finding is stored and shown — never file contents or full commit messages.</p>
        </Panel>
      </div>

      <Panel title="Recommended action" id="remediation">
        <ol className="steps">
          {detail.recommended_steps.map((step, index) => (
            <li key={index}>{step}</li>
          ))}
        </ol>
      </Panel>

      <div className="grid-2">
        <Panel title="Where it was detected" id="exposures">
          <ul className="timeline">
            {detail.exposures.map((exposure, index) => (
              <li key={index} className={exposure.active ? "timeline__item timeline__item--active" : "timeline__item"}>
                {exposure.kind === "pull_request" ? <GitPullRequest size={14} aria-hidden="true" /> : <GitBranch size={14} aria-hidden="true" />}
                <div>
                  <p className="strong">
                    {exposure.kind === "pull_request" ? `Pull request ${exposure.label}` : exposure.label} · {exposure.active ? "present" : "no longer present"}
                  </p>
                  <p className="muted small">
                    Since <Time value={exposure.opened_at} />
                    {exposure.closed_at ? <> · ended <Time value={exposure.closed_at} />: {exposure.closed_reason}</> : null}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Detection history" id="detections" flush>
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Scans that detected this violation</caption>
              <thead>
                <tr><th scope="col">Scan</th><th scope="col">Head</th><th scope="col">Result</th><th scope="col">Detected</th></tr>
              </thead>
              <tbody>
                {detail.detections.map((d) => (
                  <tr key={d.scan + d.detected_at}>
                    <td data-label="Scan" className="table__primary"><Link to={routes.scan(d.scan)}>View scan</Link></td>
                    <td data-label="Head"><Sha value={d.head_sha} /></td>
                    <td data-label="Result"><Badge map={SCAN_RESULT} value={d.result} compact /></td>
                    <td data-label="Detected"><Time value={d.detected_at} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <Panel title="Review" id="review">
        {detail.acknowledgement ? (
          <p>
            <CircleDashed size={14} aria-hidden="true" /> Acknowledged by <span className="strong">{detail.acknowledgement.by ?? "unknown"}</span> <Time value={detail.acknowledgement.at} />
            {detail.acknowledgement.note ? <>: “{detail.acknowledgement.note}”</> : null}
          </p>
        ) : (
          <p className="muted">Not reviewed yet.</p>
        )}
        <p className="muted small">Acknowledging records that someone reviewed this violation. It does not change enforcement: the GitHub check still fails and the violation stays open until the commit is gone.</p>
        {mutationError ? <Notice tone="danger">{mutationError instanceof ApiError ? mutationError.message : "The change could not be saved."}</Notice> : null}
        {v.status !== "resolved" && (detail.can_manage || detail.acknowledgement) ? (
          detail.acknowledgement ? (
            detail.can_manage ? (
              <button type="button" className="button button--secondary" onClick={() => unacknowledge.mutate()} disabled={unacknowledge.isPending}>
                Remove acknowledgement
              </button>
            ) : null
          ) : (
            <form
              className="ack-form"
              onSubmit={(event) => {
                event.preventDefault();
                acknowledge.mutate();
              }}
            >
              <div className="field">
                <label htmlFor="ack-note">Note (optional)</label>
                <textarea id="ack-note" value={note} maxLength={500} rows={2} onChange={(e) => setNote(e.target.value)} />
              </div>
              <button type="submit" className="button button--primary" disabled={acknowledge.isPending}>
                Acknowledge
              </button>
            </form>
          )
        ) : null}
      </Panel>
    </>
  );
}
