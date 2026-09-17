import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Save, Users } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";

import type { ApiError } from "../api/client";
import { getSettings, updateSettings } from "../api/governance";
import { listRules } from "../api/rules";
import type { OrganizationSettings as Settings, PolicyAction, SettingsView } from "../api/types";
import { ActionError, isApiError } from "../components/ActionError";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { OrganizationGate } from "../components/OrganizationGate";
import { OrganizationRulesEditor } from "../components/OrganizationRulesEditor";
import { Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { ScanSchedules } from "../components/ScanSchedules";
import { QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { RULE_OPTIONS, SEVERITY_OPTIONS } from "../lib/labels";
import { routes } from "../lib/routes";

type Key = keyof Settings;

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** The relaxed controls the server lists in `error.details.changes`; the message itself when it lists none. */
export function relaxedControls(error: ApiError): string[] {
  const changes = error.details?.changes;
  const items = Array.isArray(changes) ? changes.filter((item): item is string => typeof item === "string" && item.trim() !== "") : [];
  return items.length ? items : [error.message];
}

function timeZones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone") ?? ["UTC"];
  } catch {
    return ["UTC"];
  }
}

function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <fieldset className="settings-section">
      <legend className="settings-section__title">{title}</legend>
      {description ? <p className="muted small">{description}</p> : null}
      <div className="settings-section__fields">{children}</div>
    </fieldset>
  );
}

function Toggle({ label, detail, checked, onChange }: { label: string; detail?: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="checkbox">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        {label}
        {detail ? <span className="muted small block">{detail}</span> : null}
      </span>
    </label>
  );
}

function SettingsForm({ scope, view, onSaved }: { scope: GovernanceScope; view: SettingsView; onSaved: (version: number) => void }) {
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["rules"], queryFn: listRules });
  const zones = useMemo(() => timeZones(), []);
  const [form, setForm] = useState<Settings>(view.settings);
  const [warningDays, setWarningDays] = useState(view.settings.exception_warning_days.join(", "));
  const [reason, setReason] = useState("");
  const [relaxed, setRelaxed] = useState<string[] | null>(null);
  const [understood, setUnderstood] = useState(false);
  const set = <K extends Key>(key: K, value: Settings[K]) => setForm((current) => ({ ...current, [key]: value }));

  const parsedDays = warningDays
    .split(",")
    .map((d) => d.trim())
    .filter(Boolean)
    .map(Number);
  const daysValid = parsedDays.every((d) => Number.isInteger(d));
  const next: Settings = { ...form, exception_warning_days: daysValid ? parsedDays : form.exception_warning_days };
  const changed = (Object.keys(next) as Key[]).filter((key) => !same(next[key], view.settings[key]));

  const save = useMutation({
    mutationFn: (confirm: boolean) =>
      updateSettings(scope.id, {
        expected_version: view.version,
        settings: Object.fromEntries(changed.map((key) => [key, next[key]])) as Partial<Settings>,
        reason: reason.trim() || null,
        confirm,
      }),
    onSuccess: (updated) => {
      setRelaxed(null);
      setUnderstood(false);
      onSaved(updated.version);
      queryClient.setQueryData(["governance", scope.id, "settings"], updated);
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id] });
    },
    onError: (error) => {
      if (isApiError(error, "CONFIRMATION_REQUIRED")) setRelaxed(relaxedControls(error));
    },
  });
  const readOnly = !view.can_manage;
  const pageError = save.error && !isApiError(save.error, "CONFIRMATION_REQUIRED") && relaxed === null ? save.error : null;
  const ruleName = (id: string) => rules.data?.find((r) => r.id === id)?.name ?? id;

  return (
    <>
      <p className="muted small">
        {view.version ? (
          <>
            Version {view.version} · updated <Time value={view.updated_at} /> by {view.updated_by ?? "unknown"}
          </>
        ) : (
          "Organization defaults: these settings have never been saved."
        )}
      </p>
      {readOnly ? (
        <Notice>
          <Lock size={12} aria-hidden="true" /> Your role can view these settings. Admins and owners can change them.
        </Notice>
      ) : null}
      <form
        className="settings-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (changed.length && daysValid) save.mutate(false);
        }}
      >
        <fieldset className="settings-form__all" disabled={readOnly}>
          <legend className="visually-hidden">Organization security settings</legend>
          <Section title="Security baseline" description="Mandatory requirements applied to every repository of the organization, above the organization policy. A repository can make a rule stricter, never weaker.">
            {RULE_OPTIONS.map((rule) => (
              <div className="field" key={rule}>
                <label htmlFor={`baseline-${rule}`}>
                  {ruleName(rule)} <code className="muted">{rule}</code>
                </label>
                <select
                  id={`baseline-${rule}`}
                  value={form.security_baseline[rule] ?? ""}
                  onChange={(e) => {
                    const others = Object.entries(form.security_baseline).filter(([id]) => id !== rule);
                    const entries = e.target.value ? [...others, [rule, e.target.value as PolicyAction] as const] : others;
                    // Sorted like the server's document, so an undone edit is not reported as a change.
                    set("security_baseline", Object.fromEntries([...entries].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))));
                  }}
                >
                  <option value="">No baseline requirement</option>
                  <option value="warn">At least WARN</option>
                  <option value="block">BLOCK</option>
                </select>
              </div>
            ))}
          </Section>

          <Section title="Policy approval">
            <Toggle label="Require approval before a policy change is published" detail="Changes are drafted, approved, then published; direct saves are refused." checked={form.require_policy_approval} onChange={(v) => set("require_policy_approval", v)} />
            <Toggle label="Separation of duties" detail="The author of a policy change cannot approve it." checked={form.require_separate_approver} onChange={(v) => set("require_separate_approver", v)} />
          </Section>

          <Section title="Exceptions" description="Every group- or organization-wide exception, and every permanent one, always needs approval.">
            <div className="field">
              <label htmlFor="exception-severity">Approval needed from severity</label>
              <select id="exception-severity" value={form.exception_approval_min_severity} onChange={(e) => set("exception_approval_min_severity", e.target.value as Settings["exception_approval_min_severity"])}>
                {SEVERITY_OPTIONS.map((severity) => (
                  <option key={severity} value={severity}>
                    {severity.toUpperCase()} and above
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="exception-max-days">Longest exception (days, 1-365)</label>
              <input id="exception-max-days" type="number" min={1} max={365} step={1} value={form.exception_max_days} onChange={(e) => set("exception_max_days", Number(e.target.value))} />
            </div>
            <div className="field">
              <label htmlFor="exception-warning-days">Expiry warnings (days before, comma-separated)</label>
              <input id="exception-warning-days" inputMode="numeric" value={warningDays} onChange={(e) => setWarningDays(e.target.value)} aria-invalid={!daysValid} />
              {!daysValid ? <p className="small text-warning">Enter whole numbers separated by commas, for example 7, 3, 1.</p> : null}
            </div>
            <Toggle label="Allow permanent exceptions" detail="Also requires the requester to hold exception approval permission, and approval by someone else." checked={form.allow_permanent_exceptions} onChange={(v) => set("allow_permanent_exceptions", v)} />
          </Section>

          <Section title="Onboarding" description="How repositories discovered from the GitHub App are governed.">
            <div className="field">
              <label htmlFor="onboarding-mode">Default mode for new repositories</label>
              <select id="onboarding-mode" value={form.default_onboarding_mode} onChange={(e) => set("default_onboarding_mode", e.target.value as Settings["default_onboarding_mode"])}>
                <option value="enforce">Enforce — block according to policy</option>
                <option value="monitor">Monitor — report blocks as warnings</option>
              </select>
            </div>
            <Toggle label="Onboard newly discovered repositories automatically" detail="Otherwise they wait as discovered until an administrator onboards them." checked={form.auto_onboard_new_repositories} onChange={(v) => set("auto_onboard_new_repositories", v)} />
            <div className="field">
              <label htmlFor="archived-repositories">Archived repositories</label>
              <select id="archived-repositories" value={form.archived_repositories} onChange={(e) => set("archived_repositories", e.target.value as Settings["archived_repositories"])}>
                <option value="keep">Keep visible, without scheduled scans</option>
                <option value="exclude">Exclude from onboarding</option>
              </select>
            </div>
          </Section>

          <Section title="Rollout safety" description="Defaults for staged policy rollouts. Thresholds apply once a stage has enough completed scans.">
            <Toggle label="Pause a rollout that exceeds its thresholds" checked={form.rollout_auto_pause} onChange={(v) => set("rollout_auto_pause", v)} />
            <div className="field">
              <label htmlFor="rollout-error">Maximum error rate (%)</label>
              <input id="rollout-error" type="number" min={0} max={100} step={1} value={Math.round(form.rollout_max_error_rate * 100)} onChange={(e) => set("rollout_max_error_rate", Number(e.target.value) / 100)} />
            </div>
            <div className="field">
              <label htmlFor="rollout-block">Maximum block rate (%)</label>
              <input id="rollout-block" type="number" min={0} max={100} step={1} value={Math.round(form.rollout_max_block_rate * 100)} onChange={(e) => set("rollout_max_block_rate", Number(e.target.value) / 100)} />
            </div>
            <div className="field">
              <label htmlFor="rollout-min-scans">Scans before thresholds apply</label>
              <input id="rollout-min-scans" type="number" min={1} max={10000} step={1} value={form.rollout_min_scans} onChange={(e) => set("rollout_min_scans", Number(e.target.value))} />
            </div>
            <Toggle label="Roll back automatically, not only pause" detail="Publishes a new version restoring the previous policy." checked={form.rollout_auto_rollback} onChange={(v) => set("rollout_auto_rollback", v)} />
          </Section>

          <Section title="Alerts and time">
            <Toggle label="Aggregate violation alerts" detail="E-mail and webhook violation alerts become one organization digest per rule and hour." checked={form.aggregate_violation_alerts} onChange={(v) => set("aggregate_violation_alerts", v)} />
            <div className="field">
              <label htmlFor="organization-timezone">Time zone (scan schedules and report dates)</label>
              <input id="organization-timezone" list="organization-timezones" maxLength={64} value={form.timezone} onChange={(e) => set("timezone", e.target.value)} />
              <datalist id="organization-timezones">
                {zones.map((zone) => (
                  <option key={zone} value={zone} />
                ))}
              </datalist>
            </div>
          </Section>

          {!readOnly ? (
            <div className="panel__footer settings-form__footer">
              <div className="field field--grow">
                <label htmlFor="settings-reason">Reason (recorded in the audit log; required when a control is relaxed)</label>
                <input id="settings-reason" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
              </div>
              <button type="button" className="button button--secondary" disabled={changed.length === 0 || save.isPending} onClick={() => { setForm(view.settings); setWarningDays(view.settings.exception_warning_days.join(", ")); save.reset(); }}>
                Discard
              </button>
              <button type="submit" className="button button--primary" disabled={changed.length === 0 || !daysValid || save.isPending}>
                <Save size={14} aria-hidden="true" /> {save.isPending ? "Saving…" : "Save settings"}
              </button>
            </div>
          ) : null}
        </fieldset>
      </form>
      <ActionError error={pageError} fallback="The settings could not be saved." onReload={() => void queryClient.invalidateQueries({ queryKey: ["governance", scope.id, "settings"] })} />
      <ConfirmDialog
        open={relaxed !== null}
        title="You are relaxing security controls"
        confirmLabel="Relax controls"
        onCancel={() => {
          setRelaxed(null);
          setUnderstood(false);
          save.reset();
        }}
        onConfirm={() => save.mutate(true)}
        confirmDisabled={!understood || reason.trim().length === 0}
        busy={save.isPending}
      >
        <p>CommitGuard classified this change as weakening. It relaxes:</p>
        <ul className="plain-list relaxed">
          {(relaxed ?? []).map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="muted small">A relaxing change needs a reason and a sign-in from the last 15 minutes, and sends a critical notification to administrators.</p>
        <div className="field">
          <label htmlFor="relax-reason">Reason (required, recorded in the audit log)</label>
          <textarea id="relax-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand these controls become weaker for the whole organization.
        </label>
        {relaxed !== null && save.error && !isApiError(save.error, "CONFIRMATION_REQUIRED") ? <ActionError error={save.error} fallback="The settings could not be saved." /> : null}
      </ConfirmDialog>
    </>
  );
}

function Page({ scope }: { scope: GovernanceScope }) {
  const query = useQuery({ queryKey: ["governance", scope.id, "settings"], queryFn: () => getSettings(scope.id) });
  const [saved, setSaved] = useState<number | null>(null);
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.settings}>Settings</Link>}
        title="Organization settings"
        description={`Security settings, organization rules and scan schedules of ${scope.login}. Every change is versioned and audited.`}
        actions={
          scope.has("members:read") ? (
            <Link className="button button--secondary" to={routes.organizationMembers}>
              <Users size={14} aria-hidden="true" /> Members
            </Link>
          ) : null
        }
      />
      <Panel title="Security settings" id="security-settings">
        <QueryBoundary query={query} errorTitle="We could not load the organization settings." loading={<SkeletonRows rows={6} />}>
          {(view) => (
            <>
              {saved === view.version ? <Notice tone="success">Saved as version {saved}. The change is recorded in the audit log.</Notice> : null}
              <SettingsForm key={view.version} scope={scope} view={view} onSaved={setSaved} />
            </>
          )}
        </QueryBoundary>
      </Panel>
      {scope.has("rules:read") ? (
        <Panel title="Organization rules" id="organization-rules">
          <OrganizationRulesEditor scope={scope} />
        </Panel>
      ) : null}
      {scope.has("security:read") ? (
        <Panel title="Scan schedules" id="scan-schedules" flush>
          <ScanSchedules scope={scope} />
        </Panel>
      ) : null}
    </>
  );
}

export default function OrganizationSettings() {
  useDocumentTitle("Organization settings");
  return (
    <OrganizationGate title="Organization settings" permission="organization:read">
      {(scope) => <Page key={scope.id} scope={scope} />}
    </OrganizationGate>
  );
}
