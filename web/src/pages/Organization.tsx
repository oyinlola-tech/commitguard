import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, CircleCheck, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { acknowledgeEvent, getSecurityOverview, listSecurityEvents } from "../api/governance";
import type { OrganizationPosture, SecurityEvent } from "../api/types";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { OrganizationGate } from "../components/OrganizationGate";
import { KeyValueList, Metric, PageHeader, Panel, Time } from "../components/Primitives";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { count, humanize, plural } from "../lib/format";
import { INSTALLATION_STATE, POLICY_ACTION, POSTURE, POSTURE_OPTIONS, PROPAGATION, PROPAGATION_OPTIONS, PROTECTION, SEVERITY, SYNC_HEALTH } from "../lib/labels";
import { routes } from "../lib/routes";

const PROTECTION_ORDER = ["protected", "at_risk", "unprotected", "configuration_error", "unknown"] as const;

function matrix(filter: Record<string, string>): string {
  return `${routes.organizationRepositories}?${new URLSearchParams(filter).toString()}`;
}

function PolicyStatus({ data }: { data: OrganizationPosture }) {
  const policy = data.policy;
  const propagation = PROPAGATION_OPTIONS.filter((state) => (policy.propagation[state] ?? 0) > 0);
  const baseline = Object.entries(policy.baseline);
  return (
    <KeyValueList
      items={[
        [
          "Organization policy",
          policy.organization_version ? (
            <>
              v{policy.organization_version} · <Time value={policy.updated_at} /> by {policy.updated_by ?? "unknown"}
            </>
          ) : (
            <span className="muted">No organization policy published</span>
          ),
        ],
        ["Approvals pending", <Link to={routes.policyGovernance}>{plural(policy.approvals_pending, "policy change", "policy changes")}</Link>],
        ["Exceptions requested", <Link to={`${routes.exceptions}?status=requested`}>{plural(policy.exceptions_requested, "request", "requests")}</Link>],
        ["Rollouts in progress", <Link to={routes.policyGovernance}>{count(policy.rollouts_in_progress)}</Link>],
        [
          "Propagation",
          propagation.length ? (
            <span className="badge-list">
              {propagation.map((state) => (
                <span key={state} className="requirement">
                  <Badge map={PROPAGATION} value={state} compact /> {count(policy.propagation[state] ?? 0)}
                </span>
              ))}
            </span>
          ) : (
            <span className="muted">No repository resolved yet</span>
          ),
        ],
        [
          "Security baseline",
          baseline.length ? (
            <span className="badge-list">
              {baseline.map(([rule, action]) => (
                <span key={rule} className="requirement">
                  <code>{rule}</code> <Badge map={POLICY_ACTION} value={action} compact />
                </span>
              ))}
            </span>
          ) : (
            <span className="muted">None</span>
          ),
        ],
      ]}
    />
  );
}

function SecurityEvents({ scope }: { scope: GovernanceScope }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["governance", scope.id, "events"], queryFn: () => listSecurityEvents(scope.id), refetchInterval: 60_000 });
  const [target, setTarget] = useState<SecurityEvent | null>(null);
  const [note, setNote] = useState("");
  const canAcknowledge = scope.has("violations:manage");
  const acknowledge = useMutation({
    mutationFn: (event: SecurityEvent) => acknowledgeEvent(scope.id, event.id, note.trim() || null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id, "events"] });
      setTarget(null);
      setNote("");
    },
  });
  return (
    <>
      <QueryBoundary
        query={query}
        errorTitle="We could not load security events."
        loading={<SkeletonRows rows={3} label="Loading security events…" />}
        isEmpty={(events) => events.length === 0}
        empty={<EmptyState title="No critical or high security events." icon={<CircleCheck size={20} />} />}
      >
        {(events) => (
          <ul className="events">
            {events.slice(0, 10).map((event) => (
              <li key={event.id} className="events__item">
                <div className="events__head">
                  <Badge map={SEVERITY} value={event.severity} compact />
                  <span className="strong break">{event.title}</span>
                </div>
                <p className="small break">{event.body}</p>
                <p className="muted small">
                  <Time value={event.last_occurred_at} />
                  {event.occurrences > 1 ? ` · ${count(event.occurrences)} occurrences` : ""}
                  {event.acknowledged_at ? (
                    <>
                      {" · "}
                      <CircleCheck size={12} aria-hidden="true" /> Acknowledged by {event.acknowledged_by ?? "unknown"} <Time value={event.acknowledged_at} />
                    </>
                  ) : null}
                </p>
                {!event.acknowledged_at && canAcknowledge ? (
                  <button type="button" className="button button--ghost" onClick={() => setTarget(event)}>
                    <BellRing size={14} aria-hidden="true" /> Acknowledge
                    <span className="visually-hidden">: {event.title}</span>
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </QueryBoundary>
      <ConfirmDialog
        open={target !== null}
        tone="default"
        title="Acknowledge this event"
        confirmLabel="Acknowledge"
        onCancel={() => {
          setTarget(null);
          acknowledge.reset();
        }}
        onConfirm={() => target && acknowledge.mutate(target)}
        busy={acknowledge.isPending}
      >
        <p className="strong">{target?.title}</p>
        <p>Acknowledging records, in the audit log, that you have seen this event. It does not resolve a violation, approve a change or dismiss anything for other people.</p>
        <div className="field">
          <label htmlFor="acknowledge-note">Note (optional)</label>
          <textarea id="acknowledge-note" rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <ActionError error={acknowledge.error} fallback="The acknowledgement could not be saved." />
      </ConfirmDialog>
    </>
  );
}

function CommandCenter({ scope }: { scope: GovernanceScope }) {
  const query = useQuery({ queryKey: ["governance", scope.id, "overview"], queryFn: () => getSecurityOverview(scope.id), refetchInterval: 60_000 });
  return (
    <>
      <PageHeader
        eyebrow={scope.login}
        title="Command center"
        description="The security posture of the organization as explicit states with their reasons. CommitGuard never reduces it to a score."
        actions={
          query.data ? (
            <span className="updated">
              <span className="muted small">
                Updated <Time value={query.data.computed_at} />
              </span>
              <button type="button" className="button button--secondary" onClick={() => void query.refetch()} disabled={query.isFetching}>
                <RefreshCw size={14} aria-hidden="true" className={query.isFetching ? "spin" : undefined} /> Refresh
              </button>
            </span>
          ) : null
        }
      />
      <QueryBoundary query={query} errorTitle="We could not load the organization posture." loading={<SkeletonRows rows={8} label="Loading organization posture…" />}>
        {(data) => (
          <div className="stack-lg">
            <section className={`protection-banner protection-banner--${data.posture}`} aria-labelledby="posture-title">
              <Badge map={POSTURE} value={data.posture} />
              <div className="posture__body">
                <h2 className="posture__title" id="posture-title">
                  Organization posture
                </h2>
                <ul className="reasons">
                  {data.posture_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            </section>

            <Panel title="Compliance" id="compliance">
              <p className="compliance">{data.compliance}</p>
              <p className="muted small">
                Required repositories are onboarded, connected and not archived. A repository satisfies all mandatory controls when its posture is SECURE. This is a CommitGuard policy compliance statement, not a SOC 2, ISO 27001 or any other certification.
              </p>
            </Panel>

            <div className="metrics">
              <Metric label="Repositories" value={count(data.repositories)} detail={`${count(data.required_repositories)} required · ${plural(data.members, "member")}`} to={routes.organizationRepositories} />
              <Metric label="Monitor mode" value={count(data.monitor_mode)} detail="Violations reported, not blocked" to={matrix({ mode: "monitor" })} tone={data.monitor_mode ? "danger" : undefined} />
              <Metric label="Critical open" value={count(data.critical_open)} detail={`${count(data.high_open)} high severity open`} to={`${routes.violations}?severity=critical&status=open`} tone={data.critical_open ? "critical" : undefined} />
              <Metric
                label="Active exceptions"
                value={count(data.active_exceptions)}
                detail={`${count(data.expiring_exceptions)} expiring within 7 days · ${count(data.expired_exceptions_30d)} expired in 30 days`}
                to={`${routes.exceptions}?status=active`}
              />
              <Metric label="Policy drift" value={count(data.drift.drift ?? 0)} detail={`${count(data.drift.customized ?? 0)} customized · ${count(data.drift.unknown ?? 0)} unknown`} to={matrix({ drift: "drift" })} />
            </div>

            <div className="grid-2">
              <Panel title="Repositories by posture" id="by-posture">
                <ul className="breakdown">
                  {POSTURE_OPTIONS.map((posture) => (
                    <li key={posture} className="breakdown__item">
                      <Badge map={POSTURE} value={posture} compact />
                      <Link to={matrix({ posture })} className="breakdown__count">
                        {plural(data.by_posture[posture] ?? 0, "repository", "repositories")}
                      </Link>
                    </li>
                  ))}
                </ul>
              </Panel>
              <Panel title="Repositories by protection" id="by-protection">
                <ul className="breakdown">
                  {PROTECTION_ORDER.map((protection) => (
                    <li key={protection} className="breakdown__item">
                      <Badge map={PROTECTION} value={protection} compact />
                      <Link to={matrix({ protection })} className="breakdown__count">
                        {plural(data.by_protection[protection] ?? 0, "repository", "repositories")}
                      </Link>
                    </li>
                  ))}
                </ul>
                <p className="muted small">Protection is evidence from GitHub that a CommitGuard check is required before merging.</p>
              </Panel>
            </div>

            <div className="grid-2">
              <Panel title="GitHub installations" id="installations" actions={<Link to={routes.installations}>Installations</Link>}>
                {data.installations.length ? (
                  <ul className="health health--wide">
                    {data.installations.map((installation) => (
                      <li key={installation.installation_id} className="health__item">
                        <span className="stack">
                          <Badge map={INSTALLATION_STATE} value={installation.state} compact />
                          <Badge map={SYNC_HEALTH} value={installation.sync} compact />
                        </span>
                        <div>
                          <p className="strong">
                            <Link to={routes.installation(installation.installation_id)}>{installation.account_login}</Link> <span className="muted small">#{installation.installation_id}</span>
                          </p>
                          <p className="muted small">{installation.sync_detail}</p>
                          <p className="muted small">
                            {plural(installation.repositories, "repository", "repositories")} · last synchronised <Time value={installation.last_success_at} />
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState title="No GitHub installation." />
                )}
              </Panel>
              <Panel title="Policy status" id="policy-status" actions={<Link to={routes.policyGovernance}>Policy governance</Link>}>
                <PolicyStatus data={data} />
              </Panel>
            </div>

            <div className="grid-2">
              <Panel title="Critical and high security events" id="security-events">
                <SecurityEvents scope={scope} />
              </Panel>
              {scope.has("audit:read") ? (
                <Panel title="Recent governance activity" id="activity" actions={<Link to={routes.organizationAudit}>Organization audit</Link>}>
                  {data.recent_activity.length ? (
                    <ul className="plain-list activity">
                      {data.recent_activity.map((item) => (
                        <li key={item.id}>
                          <Link to={`${routes.organizationAudit}?type=${encodeURIComponent(item.type)}`} className="strong">
                            {humanize(item.type)}
                          </Link>
                          <span className="muted small">
                            {" "}
                            · {item.actor ?? "CommitGuard"} · <Time value={item.occurred_at} />
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <EmptyState title="No recent governance activity." />
                  )}
                </Panel>
              ) : null}
            </div>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}

export default function Organization() {
  useDocumentTitle("Command center");
  return (
    <OrganizationGate title="Command center" permission="security:read">
      {(scope) => <CommandCenter key={scope.id} scope={scope} />}
    </OrganizationGate>
  );
}
