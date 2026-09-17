import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CircleSlash, Pencil } from "lucide-react";
import { useState } from "react";

import { listRepositoryMatrix } from "../api/governance";
import { listGroups } from "../api/groups";
import { createSchedule, disableSchedule, listSchedules, updateSchedule, type ScheduleFields } from "../api/schedules";
import type { PolicyTargetType, ScanSchedule } from "../api/types";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { count, humanize, plural } from "../lib/format";
import { ENABLED_STATE, SCHEDULE_RUN_STATE, TARGET_TYPE_LABEL } from "../lib/labels";
import { ActionError } from "./ActionError";
import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { Time } from "./Primitives";
import { EmptyState, QueryBoundary, SkeletonRows } from "./States";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const pad = (n: number) => String(n).padStart(2, "0");

export function cadenceText(schedule: Pick<ScanSchedule, "cadence" | "hour" | "minute" | "weekday" | "timezone">): string {
  const time = `${pad(schedule.hour)}:${pad(schedule.minute)} ${schedule.timezone}`;
  return schedule.cadence === "weekly" ? `Weekly on ${WEEKDAYS[schedule.weekday ?? 0]} at ${time}` : `Daily at ${time}`;
}

function ScheduleDialog({ scope, schedule, defaultTimezone, onClose }: { scope: GovernanceScope; schedule: ScanSchedule | null; defaultTimezone: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(schedule?.name ?? "");
  const [type, setType] = useState<PolicyTargetType>(schedule?.target.type ?? "group");
  const [targetId, setTargetId] = useState(schedule?.target.id ?? "");
  const [cadence, setCadence] = useState<"daily" | "weekly">(schedule?.cadence ?? "daily");
  const [weekday, setWeekday] = useState(schedule?.weekday ?? 0);
  const [time, setTime] = useState(schedule ? `${pad(schedule.hour)}:${pad(schedule.minute)}` : "02:00");
  const [timezone, setTimezone] = useState(schedule?.timezone ?? defaultTimezone);
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const groups = useQuery({ queryKey: ["governance", scope.id, "groups", false], queryFn: () => listGroups(scope.id), enabled: !schedule && type === "group" });
  const repositories = useQuery({
    queryKey: ["governance", scope.id, "repository-picker", "", { sort: "name" }],
    queryFn: () => listRepositoryMatrix(scope.id, { sort: "name", limit: 100 }),
    enabled: !schedule && type === "repository",
  });
  const [hour, minute] = time.split(":").map(Number);
  const fields: ScheduleFields = { name: name.trim(), cadence, hour: hour ?? 0, minute: minute ?? 0, weekday: cadence === "weekly" ? weekday : null, timezone: timezone.trim(), enabled };
  const save = useMutation({
    mutationFn: () => (schedule ? updateSchedule(schedule.id, schedule.revision, fields) : createSchedule(scope.id, type, targetId || null, fields)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id, "schedules"] });
      onClose();
    },
  });
  const ready = Boolean(name.trim()) && /^\d{2}:\d{2}$/.test(time) && Boolean(timezone.trim()) && (Boolean(schedule) || type === "organization" || Boolean(targetId));
  return (
    <ConfirmDialog open tone="default" title={schedule ? `Edit ${schedule.name}` : "New scan schedule"} confirmLabel={schedule ? "Save schedule" : "Create schedule"} onCancel={onClose} onConfirm={() => save.mutate()} confirmDisabled={!ready} busy={save.isPending}>
      <div className="field">
        <label htmlFor="schedule-name">Name</label>
        <input id="schedule-name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      {schedule ? (
        <p className="small">
          Target: {TARGET_TYPE_LABEL[schedule.target.type]} · {schedule.target.label} <span className="muted">(the target cannot be changed)</span>
        </p>
      ) : (
        <>
          <div className="field">
            <label htmlFor="schedule-target-type">Target</label>
            <select id="schedule-target-type" value={type} onChange={(e) => { setType(e.target.value as PolicyTargetType); setTargetId(""); }}>
              <option value="organization">Every repository of the organization</option>
              <option value="group">A repository group</option>
              <option value="repository">One repository</option>
            </select>
          </div>
          {type === "group" ? (
            <div className="field">
              <label htmlFor="schedule-group">Group</label>
              <select id="schedule-group" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                <option value="">Choose a group</option>
                {(groups.data ?? []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>
            </div>
          ) : null}
          {type === "repository" ? (
            <div className="field">
              <label htmlFor="schedule-repository">Repository</label>
              <select id="schedule-repository" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                <option value="">Choose a repository</option>
                {(repositories.data?.items ?? []).map((r) => <option key={r.repository_id} value={String(r.repository_id)}>{r.full_name}</option>)}
              </select>
            </div>
          ) : null}
        </>
      )}
      <div className="inline-fields">
        <div className="field">
          <label htmlFor="schedule-cadence">Cadence</label>
          <select id="schedule-cadence" value={cadence} onChange={(e) => setCadence(e.target.value as "daily" | "weekly")}>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
        </div>
        {cadence === "weekly" ? (
          <div className="field">
            <label htmlFor="schedule-weekday">Day</label>
            <select id="schedule-weekday" value={weekday} onChange={(e) => setWeekday(Number(e.target.value))}>
              {WEEKDAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
            </select>
          </div>
        ) : null}
        <div className="field">
          <label htmlFor="schedule-time">Time</label>
          <input id="schedule-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="schedule-timezone">Time zone</label>
          <input id="schedule-timezone" maxLength={64} value={timezone} onChange={(e) => setTimezone(e.target.value)} />
        </div>
      </div>
      {schedule ? (
        <label className="checkbox">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled
        </label>
      ) : null}
      <p className="muted small">Scheduled scans re-evaluate default branches with the current effective policy. Archived repositories, paused monitoring and heads already scanned with the current policy are skipped.</p>
      <ActionError error={save.error} fallback="The schedule could not be saved." onReload={() => void queryClient.invalidateQueries({ queryKey: ["governance", scope.id, "schedules"] })} />
    </ConfirmDialog>
  );
}

function LastRun({ schedule }: { schedule: ScanSchedule }) {
  const run = schedule.last_run;
  if (!run) return <span className="muted">Not run yet</span>;
  const details = Object.entries(run.detail);
  return (
    <span className="stack">
      <span>
        <Badge map={SCHEDULE_RUN_STATE} value={run.state} compact /> <span className="small"><Time value={run.started_at} /></span>
      </span>
      <span className="muted small">
        {count(run.repositories)} covered · {count(run.queued)} queued · {count(run.skipped)} skipped · {count(run.failed)} failed
      </span>
      {details.length ? <span className="muted small">{details.map(([reason, n]) => `${humanize(reason)}: ${count(n)}`).join(" · ")}</span> : null}
    </span>
  );
}

export function ScanSchedules({ scope }: { scope: GovernanceScope }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["governance", scope.id, "schedules"], queryFn: () => listSchedules(scope.id) });
  const [editing, setEditing] = useState<ScanSchedule | "new" | null>(null);
  const [disabling, setDisabling] = useState<ScanSchedule | null>(null);
  const disable = useMutation({
    mutationFn: (schedule: ScanSchedule) => disableSchedule(schedule.id),
    onSuccess: () => {
      setDisabling(null);
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id, "schedules"] });
    },
  });
  const manage = scope.has("security:manage");
  const timezone = (queryClient.getQueryData(["governance", scope.id, "settings"]) as { settings?: { timezone?: string } } | undefined)?.settings?.timezone ?? "UTC";
  return (
    <>
      <QueryBoundary
        query={query}
        errorTitle="We could not load scan schedules."
        loading={<SkeletonRows rows={2} />}
        isEmpty={(items) => items.length === 0}
        empty={<EmptyState title="No scan schedules." icon={<CalendarClock size={20} />}>Schedules re-scan default branches daily or weekly, so policy changes are applied to code that is not pushed again.</EmptyState>}
      >
        {(items) => (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Scan schedules</caption>
              <thead>
                <tr><th scope="col">Schedule</th><th scope="col">Target</th><th scope="col">When</th><th scope="col">Status</th><th scope="col">Next run</th><th scope="col">Last run</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <tr key={s.id}>
                    <td data-label="Schedule" className="table__primary"><span className="strong break">{s.name}</span></td>
                    <td data-label="Target"><span className="stack"><span className="break">{s.target.label}</span><span className="muted small">{plural(s.repositories_covered, "repository", "repositories")}</span></span></td>
                    <td data-label="When">{cadenceText(s)}</td>
                    <td data-label="Status"><Badge map={ENABLED_STATE} value={s.enabled ? "enabled" : "disabled"} compact /></td>
                    <td data-label="Next run">{s.enabled && s.next_run_at ? <Time value={s.next_run_at} /> : <span className="muted">—</span>}</td>
                    <td data-label="Last run"><LastRun schedule={s} /></td>
                    <td data-label="Actions" className="align-end">
                      {manage && s.can_manage ? (
                        <span className="row-actions">
                          <button type="button" className="icon-button" aria-label={`Edit ${s.name}`} title="Edit" onClick={() => setEditing(s)}>
                            <Pencil size={14} aria-hidden="true" />
                          </button>
                          {s.enabled ? (
                            <button type="button" className="icon-button" aria-label={`Disable ${s.name}`} title="Disable" onClick={() => setDisabling(s)}>
                              <CircleSlash size={14} aria-hidden="true" />
                            </button>
                          ) : null}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryBoundary>
      {manage ? (
        <div className="panel__inset panel__footer">
          <button type="button" className="button button--secondary" onClick={() => setEditing("new")}>
            <CalendarClock size={14} aria-hidden="true" /> New schedule
          </button>
        </div>
      ) : null}
      {editing ? <ScheduleDialog scope={scope} schedule={editing === "new" ? null : editing} defaultTimezone={timezone} onClose={() => setEditing(null)} /> : null}
      <ConfirmDialog open={disabling !== null} title={`Disable ${disabling?.name ?? "schedule"}?`} confirmLabel="Disable schedule" onCancel={() => setDisabling(null)} onConfirm={() => disabling && disable.mutate(disabling)} busy={disable.isPending}>
        <p>No further scheduled scans run for this schedule. Its run history is kept, and it can be enabled again.</p>
        <ActionError error={disable.error} fallback="The schedule could not be disabled." />
      </ConfirmDialog>
    </>
  );
}
