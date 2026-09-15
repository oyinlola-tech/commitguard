import { getPage, send } from "./client";
import type { Member, Page, Role } from "./types";

export const listMembers = (organization: number, cursor?: string | null): Promise<Page<Member>> =>
  getPage<Member>(`/organizations/${encodeURIComponent(String(organization))}/members`, { cursor });
export const setMemberRole = (organization: number, userId: number, role: Role, login?: string) =>
  send<Member>("PUT", `/organizations/${encodeURIComponent(String(organization))}/members/${encodeURIComponent(String(userId))}`, { role, login });
export const removeMember = (organization: number, userId: number) =>
  send<{ removed: boolean }>("DELETE", `/organizations/${encodeURIComponent(String(organization))}/members/${encodeURIComponent(String(userId))}`);
