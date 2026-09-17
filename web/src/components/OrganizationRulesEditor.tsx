import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Save, Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { getOrganizationRules, listOrganizationRuleVersions, updateOrganizationRules } from "../api/organizationRules";
import type { OrganizationIdentity, OrganizationRuleDocument, OrganizationRules } from "../api/types";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { plural } from "../lib/format";
import { ActionError } from "./ActionError";
import { KeyValueList, Notice, Time } from "./Primitives";
import { QueryBoundary, SkeletonRows } from "./States";

type Kind = keyof OrganizationRuleDocument;
type ListField = "names" | "name_prefixes" | "emails" | "github_logins";

const KIND_LABEL: Record<Kind, { title: string; singular: string; description: string }> = {
  ai_identities: {
    title: "AI agent identities",
    singular: "AI agent",
    description: "Additional AI agents, for example an internal coding agent. Detected by the bundled co-author, identity and trailer detectors and reported under the same rules (ai_coauthor, ai_identity, ai_trailer).",
  },
  bot_identities: {
    title: "Bot identities",
    singular: "bot",
    description: "Additional automation accounts, reported under bot_identity.",
  },
};

const LIST_FIELDS: [ListField, string][] = [
  ["names", "Full names"],
  ["name_prefixes", "Name prefixes"],
  ["emails", "E-mail addresses"],
  ["github_logins", "GitHub logins"],
];

const ID_PATTERN = /^[a-z][a-z0-9_]{0,47}$/;
const empty = (): OrganizationIdentity => ({ id: "", display_name: "", names: [], name_prefixes: [], emails: [], github_logins: [] });
const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);

function IdentityFields({ kind, index, entry, onChange, onRemove, readOnly }: { kind: Kind; index: number; entry: OrganizationIdentity; onChange: (entry: OrganizationIdentity) => void; onRemove: () => void; readOnly: boolean }) {
  const id = useId();
  const [texts, setTexts] = useState<Record<ListField, string>>(() => Object.fromEntries(LIST_FIELDS.map(([field]) => [field, entry[field].join("\n")])) as Record<ListField, string>);
  const validId = entry.id === "" || ID_PATTERN.test(entry.id);
  return (
    <fieldset className="identity" disabled={readOnly}>
      <legend className="identity__legend">
        {KIND_LABEL[kind].singular} {index + 1}
        {entry.display_name ? `: ${entry.display_name}` : ""}
      </legend>
      <div className="inline-fields">
        <div className="field">
          <label htmlFor={`${id}-id`}>ID (lowercase letters, digits, _)</label>
          <input id={`${id}-id`} required maxLength={48} value={entry.id} aria-invalid={!validId} onChange={(e) => onChange({ ...entry, id: e.target.value })} />
        </div>
        <div className="field field--grow">
          <label htmlFor={`${id}-name`}>Display name</label>
          <input id={`${id}-name`} required maxLength={128} value={entry.display_name} onChange={(e) => onChange({ ...entry, display_name: e.target.value })} />
        </div>
      </div>
      {!validId ? <p className="small text-warning">The ID starts with a lowercase letter and contains only lowercase letters, digits and underscores.</p> : null}
      <div className="identity__lists">
        {LIST_FIELDS.map(([field, label]) => (
          <div className="field" key={field}>
            <label htmlFor={`${id}-${field}`}>{label} (one per line)</label>
            <textarea
              id={`${id}-${field}`}
              rows={3}
              value={texts[field]}
              onChange={(e) => {
                setTexts({ ...texts, [field]: e.target.value });
                onChange({ ...entry, [field]: lines(e.target.value) });
              }}
            />
          </div>
        ))}
      </div>
      {!readOnly ? (
        <button type="button" className="button button--ghost" onClick={onRemove}>
          <Trash2 size={14} aria-hidden="true" /> Remove this {KIND_LABEL[kind].singular}
        </button>
      ) : null}
    </fieldset>
  );
}

function Editor({ scope, rules }: { scope: GovernanceScope; rules: OrganizationRules }) {
  const queryClient = useQueryClient();
  const [document, setDocument] = useState<OrganizationRuleDocument>(rules.document);
  const [keys, setKeys] = useState<Record<Kind, number[]>>(() => ({
    ai_identities: rules.document.ai_identities.map((_, i) => i),
    bot_identities: rules.document.bot_identities.map((_, i) => i),
  }));
  const [counter, setCounter] = useState(1000);
  const [reason, setReason] = useState("");
  const readOnly = !rules.can_manage;
  const dirty = JSON.stringify(document) !== JSON.stringify(rules.document);
  const save = useMutation({
    mutationFn: () => updateOrganizationRules(scope.id, rules.version, document, reason.trim()),
    onSuccess: (updated) => {
      queryClient.setQueryData(["governance", scope.id, "organization-rules"], updated);
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id] });
    },
  });
  const add = (kind: Kind) => {
    setDocument({ ...document, [kind]: [...document[kind], empty()] });
    setKeys({ ...keys, [kind]: [...keys[kind], counter] });
    setCounter(counter + 1);
  };
  const remove = (kind: Kind, index: number) => {
    setDocument({ ...document, [kind]: document[kind].filter((_, i) => i !== index) });
    setKeys({ ...keys, [kind]: keys[kind].filter((_, i) => i !== index) });
  };
  const change = (kind: Kind, index: number, entry: OrganizationIdentity) => setDocument({ ...document, [kind]: document[kind].map((e, i) => (i === index ? entry : e)) });
  const valid = (["ai_identities", "bot_identities"] as Kind[]).every((kind) => document[kind].every((e) => ID_PATTERN.test(e.id) && e.display_name.trim()));
  return (
    <>
      {(["ai_identities", "bot_identities"] as Kind[]).map((kind) => (
        <section key={kind} className="identities" aria-labelledby={`${kind}-title`}>
          <h3 className="subheading" id={`${kind}-title`}>
            {KIND_LABEL[kind].title} <span className="muted">({document[kind].length})</span>
          </h3>
          <p className="muted small">{KIND_LABEL[kind].description}</p>
          {document[kind].length === 0 ? <p className="muted small">None.</p> : null}
          {document[kind].map((entry, index) => (
            <IdentityFields key={keys[kind][index]} kind={kind} index={index} entry={entry} readOnly={readOnly} onChange={(e) => change(kind, index, e)} onRemove={() => remove(kind, index)} />
          ))}
          {!readOnly ? (
            <button type="button" className="button button--secondary" onClick={() => add(kind)}>
              <Plus size={14} aria-hidden="true" /> Add {KIND_LABEL[kind].singular}
            </button>
          ) : null}
        </section>
      ))}
      {!readOnly ? (
        <div className="panel__footer settings-form__footer">
          <div className="field field--grow">
            <label htmlFor="rules-reason">Reason (required, recorded in the audit log)</label>
            <input id="rules-reason" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <button type="button" className="button button--primary" onClick={() => save.mutate()} disabled={!dirty || !valid || !reason.trim() || save.isPending}>
            <Save size={14} aria-hidden="true" /> {save.isPending ? "Publishing…" : `Publish rules v${rules.version + 1}`}
          </button>
        </div>
      ) : (
        <p className="muted small">
          <Lock size={12} aria-hidden="true" /> Your role can view organization rules. Admins and owners can change them.
        </p>
      )}
      <ActionError error={save.error} fallback="The organization rules could not be published." onReload={() => void queryClient.invalidateQueries({ queryKey: ["governance", scope.id, "organization-rules"] })} />
    </>
  );
}

/** Organization identity rules: exact-match data added to the bundled rules, versioned and audited. */
export function OrganizationRulesEditor({ scope }: { scope: GovernanceScope }) {
  const query = useQuery({ queryKey: ["governance", scope.id, "organization-rules"], queryFn: () => getOrganizationRules(scope.id) });
  const history = useQuery({ queryKey: ["governance", scope.id, "organization-rules", "history"], queryFn: () => listOrganizationRuleVersions(scope.id) });
  return (
    <QueryBoundary query={query} errorTitle="We could not load organization rules." loading={<SkeletonRows rows={3} />}>
      {(rules) => (
        <>
          <Notice title="Data, not code">
            Organization rules add identities to the bundled detection rules. Values are compared exactly after normalisation: there are no regular expressions, wildcards, expressions or scripts, so a rule cannot run code. An alias already claimed by a bundled agent is rejected.
          </Notice>
          <KeyValueList
            items={[
              ["Version", rules.version ? <>v{rules.version} · {rules.created_by ?? "unknown"} · <Time value={rules.created_at} /></> : <span className="muted">No organization rules published</span>],
              ["Rules version recorded with scans", <code className="break">{rules.rules_version}</code>],
              ["Reason", rules.reason ? `“${rules.reason}”` : <span className="muted">—</span>],
            ]}
          />
          <details className="disclosure">
            <summary>Trust levels</summary>
            <div className="table-wrap">
              <table className="table table--simple">
                <caption className="visually-hidden">Rule trust levels</caption>
                <thead>
                  <tr><th scope="col">Level</th><th scope="col">Source</th><th scope="col">Can</th></tr>
                </thead>
                <tbody>
                  {rules.trust_levels.map((t) => (
                    <tr key={t.level}>
                      <td data-label="Level"><code>{t.level}</code></td>
                      <td data-label="Source">{t.source}</td>
                      <td data-label="Can">{t.can}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <Editor key={rules.version} scope={scope} rules={rules} />
          <details className="disclosure">
            <summary>Version history ({plural(history.data?.length ?? 0, "version")})</summary>
            {history.data?.length ? (
              <ol className="plain-list">
                {history.data.map((v) => (
                  <li key={v.version} className="small">
                    <span className="strong">v{v.version}</span> · {v.created_by ?? "unknown"} · <Time value={v.created_at} />
                    {v.reason ? <span className="muted"> · “{v.reason}”</span> : null}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted small">No version has been published.</p>
            )}
          </details>
        </>
      )}
    </QueryBoundary>
  );
}
