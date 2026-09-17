import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { listRepositoryMatrix, type MatrixFilters } from "../api/governance";
import { count } from "../lib/format";
import { SkeletonRows, ErrorState } from "./States";

export interface RepositoryOption {
  id: number;
  full_name: string;
  detail?: string;
}

/** A labelled, keyboard-operable checkbox list of repositories. */
export function RepositoryChecklist({
  legend,
  options,
  selected,
  onChange,
  emptyText = "No repositories match.",
}: {
  legend: string;
  options: RepositoryOption[];
  selected: Map<number, string>;
  onChange: (selected: Map<number, string>) => void;
  emptyText?: string;
}) {
  const id = useId();
  const toggle = (option: RepositoryOption, checked: boolean) => {
    const next = new Map(selected);
    if (checked) next.set(option.id, option.full_name);
    else next.delete(option.id);
    onChange(next);
  };
  return (
    <fieldset className="checklist">
      <legend className="visually-hidden">{legend}</legend>
      {options.length === 0 ? (
        <p className="muted small checklist__empty">{emptyText}</p>
      ) : (
        <ul className="checklist__items">
          {options.map((option) => (
            <li key={option.id}>
              <label className="checklist__item" htmlFor={`${id}-${option.id}`}>
                <input id={`${id}-${option.id}`} type="checkbox" checked={selected.has(option.id)} onChange={(e) => toggle(option, e.target.checked)} />
                <span className="stack">
                  <span className="strong break">{option.full_name}</span>
                  {option.detail ? <span className="muted small">{option.detail}</span> : null}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

/**
 * Repositories of the organization the user can see, searched on the server
 * (at most 100 per search), with the selection kept across searches.
 */
export function RepositoryPicker({
  organization,
  legend,
  selected,
  onChange,
  filters = {},
  exclude,
}: {
  organization: number;
  legend: string;
  selected: Map<number, string>;
  onChange: (selected: Map<number, string>) => void;
  filters?: MatrixFilters;
  exclude?: Set<number>;
}) {
  const searchId = useId();
  const [q, setQ] = useState("");
  const query = useQuery({
    queryKey: ["governance", organization, "repository-picker", q, filters],
    queryFn: () => listRepositoryMatrix(organization, { ...filters, q: q || undefined, sort: "name", limit: 100 }),
    placeholderData: (previous) => previous,
  });
  const options = (query.data?.items ?? [])
    .filter((r) => !exclude?.has(r.repository_id))
    .map((r) => ({ id: r.repository_id, full_name: r.full_name, detail: `${r.onboarding} · ${r.mode}${r.groups.length ? ` · ${r.groups.map((g) => g.name).join(", ")}` : ""}` }));
  return (
    <div className="picker">
      <div className="field">
        <label htmlFor={searchId}>Search repositories</label>
        <input id={searchId} type="search" value={q} maxLength={100} placeholder="owner/name" onChange={(e) => setQ(e.target.value.slice(0, 100))} />
      </div>
      {query.isPending ? (
        <SkeletonRows rows={3} label="Loading repositories…" />
      ) : query.error ? (
        <ErrorState title="We could not load repositories." error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <>
          <RepositoryChecklist legend={legend} options={options} selected={selected} onChange={onChange} />
          {query.data?.nextCursor ? <p className="muted small">Showing the first 100 matches. Refine the search to find others.</p> : null}
        </>
      )}
      <p className="muted small" aria-live="polite">
        {count(selected.size)} selected
      </p>
    </div>
  );
}
