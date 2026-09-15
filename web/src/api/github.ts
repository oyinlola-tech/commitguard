import { getData, getPage, send } from "./client";
import type { Installation, InstallationDetail, InstallationRepository, Page, SyncResult } from "./types";

export const listInstallations = (organization?: number | null): Promise<Installation[]> =>
  getData<Installation[]>("/github/installations", { organization });
export const getInstallation = (id: number): Promise<InstallationDetail> =>
  getData<InstallationDetail>(`/github/installations/${encodeURIComponent(String(id))}`);
export const listInstallationRepositories = (id: number, cursor?: string | null): Promise<Page<InstallationRepository>> =>
  getPage<InstallationRepository>(`/github/installations/${encodeURIComponent(String(id))}/repositories`, { cursor });
export const syncInstallation = (id: number) =>
  send<SyncResult>("POST", `/github/installations/${encodeURIComponent(String(id))}/sync`);
