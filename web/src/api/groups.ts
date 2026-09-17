import { getData, request, send } from "./client";
import { org } from "./governance";
import type { RepositoryGroup, RepositoryGroupDetail } from "./types";

const group = (id: string) => `/repository-groups/${encodeURIComponent(id)}`;

export const listGroups = (organization: number, archived = false): Promise<RepositoryGroup[]> =>
  getData<RepositoryGroup[]>(`${org(organization)}/repository-groups`, { archived: archived ? "true" : undefined });
export const getGroup = (id: string): Promise<RepositoryGroupDetail> => getData<RepositoryGroupDetail>(group(id));
export const createGroup = (organization: number, name: string, description: string | null) =>
  send<RepositoryGroup>("POST", `${org(organization)}/repository-groups`, { name, description }).then((r) => r.data);
export const updateGroup = (id: string, changes: { name?: string; description?: string }) =>
  send<RepositoryGroup>("PATCH", group(id), changes).then((r) => r.data);

/** Archives (never deletes). `confirm` is required when the group has a policy or active exceptions. */
export const archiveGroup = (id: string, confirm: boolean) =>
  request<{ archived: boolean }>(group(id), { method: "DELETE", query: { confirm: confirm ? "true" : undefined } }).then((r) => r.data);

export const addGroupMembers = (id: string, repositoryIds: number[]) =>
  send<RepositoryGroupDetail>("POST", `${group(id)}/repositories`, { repository_ids: repositoryIds }).then((r) => r.data);
export const removeGroupMembers = (id: string, repositoryIds: number[]) =>
  send<RepositoryGroupDetail>("POST", `${group(id)}/repositories/remove`, { repository_ids: repositoryIds }).then((r) => r.data);
