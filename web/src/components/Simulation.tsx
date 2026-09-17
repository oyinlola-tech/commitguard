import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { getSimulation, listSimulations, simulateDraft } from "../api/policyWorkflow";
import type { PolicyDraft, Simulation } from "../api/types";
import { useBackoffPolling } from "../hooks/usePolling";
import { count } from "../lib/format";
import { SIMULATION_STATE } from "../lib/labels";
import { routes } from "../lib/routes";
import { ActionError } from "./ActionError";
import { Badge } from "./Badge";
import { Notice, Time } from "./Primitives";
import { ErrorState, SkeletonRows } from "./States";

const inProgress = (simulation: Simulation | undefined) => simulation?.state === "queued" || simulation?.state === "running";

/** An estimate from recorded scans, labelled as such everywhere it is shown. */
export function SimulationView({ simulation }: { simulation: Simulation }) {
  const result = simulation.result;
  const period = simulation.parameters.period_days;
  return (
    <section className="simulation" aria-labelledby={`simulation-${simulation.id}`}>
      <div className="simulation__head">
        <p className="simulation__label" id={`simulation-${simulation.id}`}>
          <FlaskConical size={14} aria-hidden="true" /> SIMULATION
        </p>
        <Badge map={SIMULATION_STATE} value={simulation.state} compact />
        <span className="muted small">
          {typeof period === "number" ? `Last ${count(period)} days · ` : ""}requested by {simulation.requested_by ?? "unknown"} <Time value={simulation.requested_at} />
        </span>
      </div>
      {inProgress(simulation) ? (
        <p className="muted small" aria-live="polite">
          CommitGuard re-evaluates recorded findings in the background. Nothing is enforced, and no check, violation or policy changes. This page checks the progress automatically.
        </p>
      ) : null}
      {simulation.state === "failed" ? <Notice tone="danger" title="The simulation failed">{simulation.error ?? "No estimate is available. Nothing was changed."}</Notice> : null}
      {result ? (
        <>
          <p className="simulation__disclaimer">{result.disclaimer}</p>
          <div className="metrics metrics--small">
            <div className="metric"><span className="metric__label">New blocks</span><span className="metric__value">{count(result.new_blocks)}</span><span className="metric__detail">findings that would be blocked</span></div>
            <div className="metric"><span className="metric__label">New warnings</span><span className="metric__value">{count(result.new_warnings)}</span><span className="metric__detail">findings that would warn</span></div>
            <div className="metric"><span className="metric__label">No longer blocked</span><span className="metric__value">{count(result.no_longer_blocked)}</span><span className="metric__detail">blocked today, allowed or warned</span></div>
            <div className="metric"><span className="metric__label">Unchanged</span><span className="metric__value">{count(result.unchanged)}</span><span className="metric__detail">findings with the same action</span></div>
          </div>
          <dl className="kv kv--compact">
            <div className="kv__row"><dt>Scans newly blocked</dt><dd>{count(result.scans_newly_blocked)}</dd></div>
            <div className="kv__row"><dt>Scans no longer blocked</dt><dd>{count(result.scans_no_longer_blocked)}</dd></div>
            <div className="kv__row"><dt>Analyzed</dt><dd>{count(result.repositories_analyzed)} repositories · {count(result.scans_analyzed)} scans · {count(result.findings_analyzed)} findings</dd></div>
            <div className="kv__row"><dt>Repositories without data</dt><dd>{count(result.repositories_without_data)} (no scan in the period)</dd></div>
            <div className="kv__row">
              <dt>Scans with assumed defaults</dt>
              <dd>{count(result.scans_assumed_defaults)} (recorded without the repository&apos;s own configuration, so built-in defaults were assumed)</dd>
            </div>
          </dl>
          {result.truncated ? <Notice tone="warning" title="Partial estimate">The period had more scans or findings than a simulation analyzes; only part of them were re-evaluated.</Notice> : null}
          {result.most_affected.length ? (
            <div className="table-wrap">
              <table className="table table--simple">
                <caption className="visually-hidden">Most affected repositories (simulation)</caption>
                <thead>
                  <tr><th scope="col">Repository</th><th scope="col">Scans</th><th scope="col">New blocks</th><th scope="col">New warnings</th><th scope="col">No longer blocked</th></tr>
                </thead>
                <tbody>
                  {result.most_affected.map((impact) => (
                    <tr key={impact.repository_id}>
                      <td data-label="Repository">{impact.full_name ? <Link to={routes.repository(impact.repository_id)}>{impact.full_name}</Link> : <span className="muted">A repository you cannot see</span>}</td>
                      <td data-label="Scans">{count(impact.scans)}</td>
                      <td data-label="New blocks">{count(impact.new_blocks)}</td>
                      <td data-label="New warnings">{count(impact.new_warnings)}</td>
                      <td data-label="No longer blocked">{count(impact.no_longer_blocked)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted small">No repository would be affected in this period.</p>
          )}
        </>
      ) : null}
    </section>
  );
}

function PolledSimulation({ id, initial }: { id: string; initial?: Simulation }) {
  const refetchInterval = useBackoffPolling(inProgress);
  const query = useQuery({ queryKey: ["governance", "simulation", id], queryFn: () => getSimulation(id), refetchInterval, initialData: initial });
  if (query.isPending) return <SkeletonRows rows={3} label="Loading simulation…" />;
  if (query.error || !query.data) return <ErrorState title="We could not load the simulation." error={query.error} onRetry={() => void query.refetch()} />;
  return <SimulationView simulation={query.data} />;
}

/** Run a read-only simulation of a draft and show the latest one. */
export function SimulationPanel({ draft, canRun }: { draft: PolicyDraft; canRun: boolean }) {
  const queryClient = useQueryClient();
  const [period, setPeriod] = useState(30);
  const [latest, setLatest] = useState<Simulation | null>(null);
  const list = useQuery({ queryKey: ["governance", draft.organization_id, "simulations", draft.id], queryFn: () => listSimulations(draft.organization_id, draft.id) });
  const run = useMutation({
    mutationFn: () => simulateDraft(draft.id, period),
    onSuccess: (simulation) => {
      setLatest(simulation);
      void queryClient.invalidateQueries({ queryKey: ["governance", draft.organization_id, "simulations", draft.id] });
    },
  });
  const shown = latest ?? list.data?.[0] ?? null;
  const open = draft.state !== "published" && draft.state !== "cancelled";
  return (
    <>
      <p className="muted small">
        A simulation re-evaluates the findings of recorded scans under this draft and under the current policy. It is read-only: it never publishes, changes checks or violations, or runs detectors.
      </p>
      {canRun && open ? (
        <div className="inline-fields">
          <div className="field">
            <label htmlFor="simulation-period">Period</label>
            <select id="simulation-period" value={period} onChange={(e) => setPeriod(Number(e.target.value))}>
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
          </div>
          <button type="button" className="button button--secondary" onClick={() => run.mutate()} disabled={run.isPending || inProgress(shown ?? undefined)}>
            <FlaskConical size={14} aria-hidden="true" /> {run.isPending ? "Queuing…" : "Run simulation"}
          </button>
        </div>
      ) : null}
      <ActionError error={run.error} fallback="The simulation could not be queued." />
      {list.isPending && !latest ? (
        <SkeletonRows rows={2} label="Loading simulations…" />
      ) : shown ? (
        <PolledSimulation key={shown.id} id={shown.id} initial={shown} />
      ) : (
        <p className="muted small">No simulation has been run for this draft.</p>
      )}
    </>
  );
}
