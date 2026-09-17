import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { createBulkOperation, idempotencyKey } from "../api/bulk";
import { getSettings } from "../api/governance";
import { listGroups } from "../api/groups";
import type { BulkOperation, RepositoryMode } from "../api/types";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { BulkProgress, ENFORCE_WARNING, GroupSelect, ModeChoice, MONITOR_WARNING } from "../components/BulkOperation";
import { OrganizationGate } from "../components/OrganizationGate";
import { Notice, PageHeader, Panel } from "../components/Primitives";
import { RepositoryPicker } from "../components/RepositoryPicker";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { count, plural } from "../lib/format";
import { REPOSITORY_MODE } from "../lib/labels";
import { routes } from "../lib/routes";

const STEPS = ["Select repositories", "Choose a group", "Choose a mode", "Review and confirm", "Apply"] as const;

function Wizard({ scope }: { scope: GovernanceScope }) {
  const [step, setStep] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [selected, setSelected] = useState<Map<number, string>>(new Map());
  const [useGroup, setUseGroup] = useState(false);
  const [group, setGroup] = useState("");
  const [mode, setMode] = useState<RepositoryMode | null>(null);
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [keys] = useState(() => ({ onboard: idempotencyKey(), group: idempotencyKey() }));
  const [operations, setOperations] = useState<BulkOperation[]>([]);
  const heading = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    heading.current?.focus();
  }, [step]);

  const settings = useQuery({ queryKey: ["governance", scope.id, "settings"], queryFn: () => getSettings(scope.id) });
  const groups = useQuery({ queryKey: ["governance", scope.id, "groups", false], queryFn: () => listGroups(scope.id) });
  const chosenMode: RepositoryMode = mode ?? settings.data?.settings.default_onboarding_mode ?? "enforce";
  const chosenGroup = useGroup ? groups.data?.find((g) => g.id === group) ?? null : null;
  const ids = [...selected.keys()];

  const submit = useMutation({
    mutationFn: async () => {
      const created: BulkOperation[] = [];
      created.push(
        await createBulkOperation(scope.id, {
          type: "onboard",
          repository_ids: ids,
          parameters: { mode: chosenMode, reason: reason.trim() || null },
          idempotency_key: keys.onboard,
          confirm: true,
        }),
      );
      setOperations([...created]);
      if (chosenGroup) {
        created.push(
          await createBulkOperation(scope.id, {
            type: "add_to_group",
            repository_ids: ids,
            parameters: { group_id: chosenGroup.id },
            idempotency_key: keys.group,
            confirm: false,
          }),
        );
      }
      return created;
    },
    onSuccess: (created) => {
      setOperations(created);
      setStep(4);
    },
  });

  const canContinue = [selected.size > 0, !useGroup || Boolean(group), chosenMode === "enforce" || reason.trim().length > 0, understood, true][step];
  const names = [...selected.values()];

  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.organizationRepositories}>Repository matrix</Link>}
        title="Add repositories"
        description="Onboard repositories into CommitGuard governance: choose them, optionally a group, and the mode they run in. Nothing changes until you confirm."
      />
      <ol className="wizard-steps" aria-label="Steps">
        {STEPS.map((label, index) => (
          <li key={label} className={index === step ? "wizard-steps__item wizard-steps__item--current" : index < step ? "wizard-steps__item wizard-steps__item--done" : "wizard-steps__item"} aria-current={index === step ? "step" : undefined}>
            <span className="wizard-steps__number" aria-hidden="true">{index + 1}</span>
            <span>{label}</span>
          </li>
        ))}
      </ol>
      <Panel>
        <h2 className="subheading wizard__heading" ref={heading} tabIndex={-1}>
          Step {step + 1} of {STEPS.length}: {STEPS[step]}
        </h2>

        {step === 0 ? (
          <>
            <p className="muted">
              Repositories are discovered from the GitHub App installation. {showAll ? "All repositories you can access are listed." : "Only repositories not yet onboarded are listed."}
            </p>
            <label className="checkbox checkbox--inline">
              <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show every repository, including onboarded and excluded ones
            </label>
            <RepositoryPicker organization={scope.id} legend="Repositories to onboard" selected={selected} onChange={setSelected} filters={showAll ? {} : { onboarding: "discovered" }} />
          </>
        ) : null}

        {step === 1 ? (
          <>
            <fieldset className="radio-group">
              <legend>Group</legend>
              <label className="radio">
                <input type="radio" name="wizard-group" checked={!useGroup} onChange={() => setUseGroup(false)} /> Do not add them to a group
              </label>
              <label className="radio">
                <input type="radio" name="wizard-group" checked={useGroup} onChange={() => setUseGroup(true)} /> Add them to a group
              </label>
            </fieldset>
            {useGroup ? <GroupSelect organization={scope.id} value={group} onChange={setGroup} allowCreate={scope.has("repositories:manage")} /> : null}
            <p className="muted small">A group&apos;s published policy applies to its repositories. Organization policy and the security baseline apply to every repository, whether or not it is in a group.</p>
          </>
        ) : null}

        {step === 2 ? (
          <>
            {settings.data ? (
              <p className="muted small">
                The organization&apos;s default onboarding mode is <Badge map={REPOSITORY_MODE} value={settings.data.settings.default_onboarding_mode} compact />.
              </p>
            ) : null}
            <ModeChoice mode={chosenMode} onChange={setMode} reason={reason} onReason={setReason} name="wizard-mode" />
          </>
        ) : null}

        {step === 3 ? (
          <>
            <ul className="plain-list review">
              <li>
                <span className="strong">{plural(ids.length, "repository", "repositories")}</span> will be onboarded in <Badge map={REPOSITORY_MODE} value={chosenMode} compact /> mode.{" "}
                <span className="muted">Repositories that are already onboarded switch to this mode.</span>
              </li>
              <li>
                {chosenGroup ? (
                  <>
                    They will be added to the group <span className="strong">{chosenGroup.name}</span>
                    {chosenGroup.policy_version ? `, whose policy v${chosenGroup.policy_version} then applies to them.` : ", which has no published policy yet."}
                  </>
                ) : (
                  "They will not be added to a group."
                )}
              </li>
            </ul>
            <details className="disclosure">
              <summary>Selected repositories ({count(names.length)})</summary>
              <ul className="plain-list">
                {names.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
            </details>
            <Notice tone="warning" title={chosenMode === "enforce" ? "Enforce mode" : "Monitor mode"}>
              {chosenMode === "enforce" ? ENFORCE_WARNING : MONITOR_WARNING}
            </Notice>
            <label className="checkbox">
              <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I reviewed the impact on these repositories.
            </label>
            <ActionError error={submit.error} fallback="The repositories could not be onboarded." />
          </>
        ) : null}

        {step === 4 ? (
          <>
            <Notice tone="success" title="Queued">
              CommitGuard applies the changes in the background and records them in the audit log.
            </Notice>
            <div className="stack-lg">
              {operations.map((operation) => (
                <BulkProgress key={operation.id} operationId={operation.id} />
              ))}
            </div>
            <Link to={routes.organizationRepositories} className="button button--secondary">
              Back to the repository matrix
            </Link>
          </>
        ) : null}

        {step < 4 ? (
          <div className="wizard__actions">
            {step > 0 ? (
              <button type="button" className="button button--secondary" onClick={() => setStep(step - 1)} disabled={submit.isPending}>
                <ArrowLeft size={14} aria-hidden="true" /> Back
              </button>
            ) : null}
            {step < 3 ? (
              <button type="button" className="button button--primary" onClick={() => setStep(step + 1)} disabled={!canContinue}>
                Next <ArrowRight size={14} aria-hidden="true" />
              </button>
            ) : (
              <button type="button" className="button button--danger" onClick={() => submit.mutate()} disabled={!canContinue || submit.isPending}>
                {submit.isPending ? "Working…" : `Onboard ${plural(ids.length, "repository", "repositories")}`}
              </button>
            )}
          </div>
        ) : null}
      </Panel>
    </>
  );
}

export default function AddRepositories() {
  useDocumentTitle("Add repositories");
  return (
    <OrganizationGate title="Add repositories" permission="repositories:manage">
      {(scope) => <Wizard key={scope.id} scope={scope} />}
    </OrganizationGate>
  );
}
