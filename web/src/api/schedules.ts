import { getData, send } from "./client";
import { org } from "./governance";
import type { PolicyTargetType, ScanSchedule, ScheduleRun } from "./types";

const schedule = (id: string) => `/scan-schedules/${encodeURIComponent(id)}`;

export interface ScheduleFields {
  name: string;
  cadence: "daily" | "weekly";
  hour: number;
  minute: number;
  weekday: number | null;
  timezone: string;
  enabled: boolean;
}

export const listSchedules = (organization: number): Promise<ScanSchedule[]> => getData<ScanSchedule[]>(`${org(organization)}/scan-schedules`);
export const getSchedule = (id: string): Promise<{ schedule: ScanSchedule; runs: ScheduleRun[] }> =>
  getData<{ schedule: ScanSchedule; runs: ScheduleRun[] }>(schedule(id));
export const createSchedule = (organization: number, type: PolicyTargetType, targetId: string | null, fields: ScheduleFields) =>
  send<ScanSchedule>("POST", `${org(organization)}/scan-schedules`, {
    ...fields,
    target_type: type,
    target_id: type === "organization" ? null : targetId,
  }).then((r) => r.data);
export const updateSchedule = (id: string, expectedRevision: number, fields: ScheduleFields) =>
  send<ScanSchedule>("PATCH", schedule(id), { expected_revision: expectedRevision, ...fields }).then((r) => r.data);
export const disableSchedule = (id: string) => send<ScanSchedule>("POST", `${schedule(id)}/disable`).then((r) => r.data);
