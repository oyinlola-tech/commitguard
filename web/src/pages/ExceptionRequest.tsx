import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { requestException } from "../api/exceptions";
import { getSettings, listRepositoryMatrix } from "../api/governance";
import { listGroups } from "../api/groups";
import { listRules } from "../api/rules";
import type { PolicyTargetType, Severity } from "../api/types";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { OrganizationGate } from "../components/OrganizationGate";
import { Notice, PageHeader, Panel } from "../components/Primitives";
import { ErrorState, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { plural } from "../lib/format";
import { SEVERITY, SEVERITY_OPTIONS, TARGET_TYPE_LABEL } from "../lib/labels";
import { routes } from "../lib/routes";

const DAY = 24 * 3600 * 1000;

/** Local calendar date `days` from today as YYYY-MM-DD (the value of a date input). */
function dateInDays(days: number, from = new Date()): string {
  const date = new Date(from.getFullYear(), from.getMonth(), from.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Start of the chosen local day, as ISO 8601. The server checks it against the organization's limit. */
function expiryIso(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(year ?? 1970, (month ?? 1) - 1, day ?? 1).toISOString();
}

/** Severity order, most severe last (the server's `Severity.rank`). */
const severityRank = (severity: Severity) => SEVERITY_OPTIONS.length - 1 - SEVERITY_OPTIONS.indexOf(severity);

function RequestForm({ scope }: { scope: GovernanceScope }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const rules = useQuery({ queryKey: ["rules"], queryFn: listRules });
  const settings = useQuery({ queryKey: ["governance", scope.id, "settings"], queryFn: () => getSettings(scope.id) });
  const initialScope = (["organization", "group", "repository"] as const).find((s) => s === params.get("scope")) ?? "repository";
  const [ruleId, setRuleId] = useState(params.get("rule") ?? "");
  const [scopeType, setScopeType] = useState<PolicyTargetType>(initialScope);
  const [scopeId, setScopeId] = useState(initialScope === "organization" ? "" : (params.get("id") ?? "").slice(0, 64));
  const [action, setAction] = useState<"warn" | "allow">("warn");
  const [reason, setReason] = useState("");
  const [expires, setExpires] = useState<string | null>(null);
  const [permanent, setPermanent] = useState(false);
  const [q, setQ] = useState("");
  const groups = useQuery({ queryKey: ["governance", scope.id, "groups", false], queryFn: () => listGroups(scope.id), enabled: scopeType === "group" });
  const repositories = useQuery({
    queryKey: ["governance", scope.id, "repository-picker", q, { sort: "name" }],
    queryFn: () => listRepositoryMatrix(scope.id, { q: q || undefined, sort: "name", limit: 100 }),
    enabled: scopeType === "repository",
    placeholderData: (previous) => previous,
  });

  const submit = useMutation({
    mutationFn: (expiresAt: string | null) =>
      requestException(scope.id, {
        rule_id: ruleId,
        scope_type: scopeType,
        scope_id: scopeType === "organization" ? null : scopeType === "repository" ? Number(scopeId) : scopeId,
        action,
        reason: reason.trim(),
        permanent,
        ...(expiresAt ? { expires_at: expiresAt } : {}),
      }),
    onSuccess: (exception) => {
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id] });
      navigate(routes.exception(exception.id));
    },
  });

  if (rules.isPending || settings.isPending) return <SkeletonRows rows={6} label="Loading the request form…" />;
  if (rules.error || settings.error || !rules.data || !settings.data) {
    return <ErrorState title="We could not load the request form." error={rules.error ?? settings.error} onRetry={() => { void rules.refetch(); void settings.refetch(); }} />;
  }

  const organization = settings.data.settings;
  const maxDays = organization.exception_max_days;
  const expiryDate = expires ?? dateInDays(Math.min(30, maxDays));
  const permanentAllowed = organization.allow_permanent_exceptions && scope.has("exceptions:approve");
  const rule = rules.data.find((r) => r.id === ruleId) ?? null;
  const reasons: string[] = [];
  if (rule && severityRank(rule.severity) >= severityRank(organization.exception_approval_min_severity)) {
    reasons.push(`the rule's severity (${rule.severity.toUpperCase()}) is at or above the organization's approval threshold (${organization.exception_approval_min_severity.toUpperCase()})`);
  }
  if (scopeType !== "repository") reasons.push(`it covers ${scopeType === "group" ? "a whole group" : "the whole organization"}`);
  if (permanent) reasons.push("it is permanent");
  const ready = Boolean(ruleId) && (scopeType === "organization" || Boolean(scopeId)) && reason.trim().length > 0 && (permanent || Boolean(expiryDate));
  const days = Math.round((Date.parse(`${expiryDate}T00:00:00Z`) - Date.parse(`${dateInDays(0)}T00:00:00Z`)) / DAY);

  return (
    <form
      className="stack-lg"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) submit.mutate(permanent ? null : expiryIso(expiryDate));
      }}
    >
      <div className="grid-2">
        <Panel title="Exception" id="exception-form">
          <div className="field">
            <label htmlFor="exception-rule">Rule</label>
            <select id="exception-rule" required value={ruleId} onChange={(e) => setRuleId(e.target.value)}>
              <option value="">Choose a rule</option>
              {rules.data.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.id}, {r.severity})
                </option>
              ))}
            </select>
          </div>
          {rule ? (
            <p className="muted small">
              <Badge map={SEVERITY} value={rule.severity} compact /> {rule.description}
            </p>
          ) : null}
          <fieldset className="radio-group">
            <legend>Scope</legend>
            {(["repository", "group", "organization"] as const).map((value) => (
              <label key={value} className="radio">
                <input
                  type="radio"
                  name="exception-scope"
                  checked={scopeType === value}
                  onChange={() => {
                    setScopeType(value);
                    setScopeId("");
                  }}
                />
                {TARGET_TYPE_LABEL[value]}
              </label>
            ))}
          </fieldset>
          {scopeType === "group" ? (
            <div className="field">
              <label htmlFor="exception-group">Group</label>
              <select id="exception-group" required value={scopeId} onChange={(e) => setScopeId(e.target.value)} disabled={groups.isPending}>
                <option value="">{groups.isPending ? "Loading groups…" : "Choose a group"}</option>
                {(groups.data ?? []).map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} ({plural(g.repository_count, "repository", "repositories")})
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          {scopeType === "repository" ? (
            <div className="inline-fields">
              <div className="field">
                <label htmlFor="exception-repository-search">Search repositories</label>
                <input id="exception-repository-search" type="search" maxLength={100} value={q} onChange={(e) => setQ(e.target.value)} placeholder="owner/name" />
              </div>
              <div className="field field--grow">
                <label htmlFor="exception-repository">Repository</label>
                <select id="exception-repository" required value={scopeId} onChange={(e) => setScopeId(e.target.value)} disabled={repositories.isPending}>
                  <option value="">{repositories.isPending ? "Loading repositories…" : "Choose a repository"}</option>
                  {(repositories.data?.items ?? []).map((r) => (
                    <option key={r.repository_id} value={String(r.repository_id)}>{r.full_name}</option>
                  ))}
                </select>
              </div>
            </div>
          ) : null}
          {scopeType === "organization" ? <p className="muted small">An organization exception applies to every repository of {scope.login}.</p> : null}
          <fieldset className="radio-group">
            <legend>Lower the rule to</legend>
            <label className="radio">
              <input type="radio" name="exception-action" checked={action === "warn"} onChange={() => setAction("warn")} /> WARN — findings are reported, checks do not fail
            </label>
            <label className="radio">
              <input type="radio" name="exception-action" checked={action === "allow"} onChange={() => setAction("allow")} /> ALLOW — findings are recorded without a warning
            </label>
          </fieldset>
          <div className="field">
            <label htmlFor="exception-reason">Justification (required, recorded in the audit log)</label>
            <textarea id="exception-reason" required rows={4} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          {!permanent ? (
            <div className="field">
              <label htmlFor="exception-expires">Expires on (at the start of the day, your time zone)</label>
              <input id="exception-expires" type="date" required min={dateInDays(1)} max={dateInDays(maxDays)} value={expiryDate} onChange={(e) => setExpires(e.target.value)} />
              <p className="muted small">
                {days > 0 ? `In ${plural(days, "day")}. ` : ""}This organization allows at most {plural(maxDays, "day")}. The exception ends automatically.
              </p>
            </div>
          ) : null}
          {permanentAllowed ? (
            <label className="checkbox">
              <input type="checkbox" checked={permanent} onChange={(e) => setPermanent(e.target.checked)} /> Permanent exception (no expiry; always needs approval by someone else)
            </label>
          ) : null}
        </Panel>
        <Panel title="Approval" id="approval">
          {!ruleId ? (
            <p className="muted">Choose a rule to see whether this exception needs approval.</p>
          ) : reasons.length ? (
            <Notice tone="warning" title="Approval will be needed">
              This exception needs approval by someone else with exception approval permission, because {reasons.join("; and ")}. It has no effect until it is approved.
            </Notice>
          ) : (
            <Notice tone="info" title="No approval expected">
              A repository exception for a rule below the organization&apos;s approval threshold ({organization.exception_approval_min_severity.toUpperCase()}) is active as soon as it is requested.
            </Notice>
          )}
          <p className="muted small">CommitGuard decides when the request is saved; the result is shown on the exception.</p>
          <h3 className="subheading">What an exception does</h3>
          <ul className="plain-list small">
            <li>Lowers one rule, in one scope, until it expires or is revoked.</li>
            <li>Is the only way below a mandatory requirement.</li>
            <li>Never removes detection: findings are still recorded, with the exception that lowered them.</li>
            <li>Never changes past scan results or resolves violations.</li>
          </ul>
        </Panel>
      </div>
      <ActionError error={submit.error} fallback="The exception could not be requested." />
      <div className="wizard__actions">
        <Link to={routes.exceptions} className="button button--secondary">
          Cancel
        </Link>
        <button type="submit" className="button button--primary" disabled={!ready || submit.isPending}>
          <Send size={14} aria-hidden="true" /> {submit.isPending ? "Requesting…" : "Request exception"}
        </button>
      </div>
    </form>
  );
}

export default function ExceptionRequest() {
  useDocumentTitle("Request an exception");
  return (
    <OrganizationGate title="Request an exception" permission="exceptions:create">
      {(scope) => (
        <>
          <PageHeader
            eyebrow={<Link to={routes.exceptions}>Exceptions</Link>}
            title="Request an exception"
            description="An exception lowers one rule to WARN or ALLOW in one repository, group or the whole organization, for a limited time."
          />
          <RequestForm key={scope.id} scope={scope} />
        </>
      )}
    </OrganizationGate>
  );
}
