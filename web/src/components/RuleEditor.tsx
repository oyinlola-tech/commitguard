import { useId } from "react";

import type { OrganizationPolicy, PolicyAction, ScopedPolicy } from "../api/types";

export type Strength = "none" | "mandatory" | "default";

export interface RuleChoice {
  strength: Strength;
  action: PolicyAction;
}

export interface EditableRule {
  policy_id: string;
  name: string;
  description?: string;
}

export type RuleChoices = Record<string, RuleChoice>;

/** The rules and their published requirements for any policy target. */
export function currentChoices(policy: OrganizationPolicy | ScopedPolicy): { rules: EditableRule[]; choices: RuleChoices } {
  if ("target" in policy) {
    return {
      rules: policy.rules.map((r) => ({ policy_id: r.policy_id, name: r.name })),
      choices: Object.fromEntries(
        policy.rules.map((r) => [
          r.policy_id,
          r.mandatory ? { strength: "mandatory", action: r.mandatory } : r.default ? { strength: "default", action: r.default } : { strength: "none", action: "warn" },
        ]),
      ) as RuleChoices,
    };
  }
  return {
    rules: policy.rules.map((r) => ({ policy_id: r.policy_id, name: r.name, description: r.description })),
    choices: Object.fromEntries(
      policy.rules.map((r) => [
        r.policy_id,
        r.organization_floor
          ? { strength: "mandatory", action: r.organization_floor }
          : r.organization_default
            ? { strength: "default", action: r.organization_default }
            : { strength: "none", action: "warn" },
      ]),
    ) as RuleChoices,
  };
}

export function choicesFromDocument(rules: EditableRule[], floors: Record<string, PolicyAction>, defaults: Record<string, PolicyAction>): RuleChoices {
  return Object.fromEntries(
    rules.map((r) => [
      r.policy_id,
      floors[r.policy_id] ? { strength: "mandatory", action: floors[r.policy_id] } : defaults[r.policy_id] ? { strength: "default", action: defaults[r.policy_id] } : { strength: "none", action: "warn" },
    ]),
  ) as RuleChoices;
}

export function documentFromChoices(choices: RuleChoices): { floors: Record<string, PolicyAction>; defaults: Record<string, PolicyAction> } {
  const floors: Record<string, PolicyAction> = {};
  const defaults: Record<string, PolicyAction> = {};
  for (const [rule, choice] of Object.entries(choices)) {
    if (choice.strength === "mandatory") floors[rule] = choice.action;
    else if (choice.strength === "default") defaults[rule] = choice.action;
  }
  return { floors, defaults };
}

export const sameChoice = (a: RuleChoice | undefined, b: RuleChoice | undefined) =>
  (a?.strength ?? "none") === (b?.strength ?? "none") && ((a?.strength ?? "none") === "none" || a?.action === b?.action);

function RuleField({
  rule,
  choice,
  original,
  onChange,
  disabled,
}: {
  rule: EditableRule;
  choice: RuleChoice;
  original: RuleChoice | undefined;
  onChange: (choice: RuleChoice) => void;
  disabled: boolean;
}) {
  const id = useId();
  const changed = original !== undefined && !sameChoice(choice, original);
  const setStrength = (strength: Strength) => {
    const action = strength === "mandatory" && choice.action === "allow" ? "warn" : choice.action;
    onChange({ strength, action });
  };
  const actions: PolicyAction[] = choice.strength === "mandatory" ? ["warn", "block"] : ["allow", "warn", "block"];
  return (
    <fieldset className="rule-editor__rule" disabled={disabled}>
      <legend className="rule-editor__legend">
        <span className="strong">{rule.name}</span> <code className="muted">{rule.policy_id}</code>
        {changed ? <span className="tag">Changed</span> : null}
      </legend>
      {rule.description ? <p className="muted small">{rule.description}</p> : null}
      <div className="rule-editor__choices">
        <label className="radio">
          <input type="radio" name={`${id}-strength`} checked={choice.strength === "none"} onChange={() => setStrength("none")} /> Repository decides
        </label>
        <label className="radio">
          <input type="radio" name={`${id}-strength`} checked={choice.strength === "mandatory"} onChange={() => setStrength("mandatory")} /> Mandatory
        </label>
        <label className="radio">
          <input type="radio" name={`${id}-strength`} checked={choice.strength === "default"} onChange={() => setStrength("default")} /> Default
        </label>
        <div className="field rule-editor__action">
          <label htmlFor={`${id}-action`}>Action for {rule.policy_id}</label>
          <select id={`${id}-action`} value={choice.strength === "none" ? "" : choice.action} disabled={disabled || choice.strength === "none"} onChange={(e) => onChange({ ...choice, action: e.target.value as PolicyAction })}>
            {choice.strength === "none" ? <option value="">Not set</option> : null}
            {actions.map((action) => (
              <option key={action} value={action}>
                {choice.strength === "mandatory" && action === "warn" ? "At least WARN" : action.toUpperCase()}
              </option>
            ))}
          </select>
        </div>
      </div>
    </fieldset>
  );
}

/** One fieldset per rule: repository decides / mandatory warn|block / default allow|warn|block. */
export function RuleEditor({
  rules,
  choices,
  original,
  onChange,
  disabled = false,
}: {
  rules: EditableRule[];
  choices: RuleChoices;
  original?: RuleChoices;
  onChange: (choices: RuleChoices) => void;
  disabled?: boolean;
}) {
  return (
    <div className="rule-editor">
      {rules.map((rule) => (
        <RuleField
          key={rule.policy_id}
          rule={rule}
          choice={choices[rule.policy_id] ?? { strength: "none", action: "warn" }}
          original={original?.[rule.policy_id]}
          disabled={disabled}
          onChange={(choice) => onChange({ ...choices, [rule.policy_id]: choice })}
        />
      ))}
    </div>
  );
}
