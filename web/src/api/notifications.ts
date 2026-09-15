import { getData, getPage, request, send } from "./client";
import type {
  CreatedWebhook,
  NotificationCounts,
  NotificationDelivery,
  NotificationItem,
  NotificationSettings,
  Page,
} from "./types";

export interface NotificationFilters {
  state?: "unread" | "read" | "archived" | "all";
  category?: string;
  organization?: number | null;
  cursor?: string | null;
  limit?: number;
}

const org = (id: number) => `/organizations/${encodeURIComponent(String(id))}`;
const note = (id: string) => `/notifications/${encodeURIComponent(id)}`;

export async function listNotifications(filters: NotificationFilters): Promise<Page<NotificationItem> & { counts: NotificationCounts | null }> {
  const envelope = await request<NotificationItem[]>("/notifications", { query: { ...filters } });
  return {
    items: envelope.data,
    nextCursor: envelope.meta.next_cursor ?? null,
    limit: envelope.meta.limit ?? envelope.data.length,
    counts: (envelope.meta.counts as NotificationCounts | undefined) ?? null,
  };
}

export const getNotificationCounts = (): Promise<NotificationCounts> => getData<NotificationCounts>("/notifications/counts");
export const markNotificationRead = (id: string) => send<NotificationItem>("POST", `${note(id)}/read`);
export const markNotificationUnread = (id: string) => send<NotificationItem>("POST", `${note(id)}/unread`);
export const archiveNotification = (id: string) => send<NotificationItem>("POST", `${note(id)}/archive`);
export const markAllNotificationsRead = (organizationId?: number | null) =>
  send<{ updated: number }>("POST", "/notifications/read-all", organizationId ? { organization_id: organizationId } : {});

export const listNotificationPreferences = (): Promise<NotificationSettings[]> => getData<NotificationSettings[]>("/notification-preferences");
export const setPersonalPreference = (organizationId: number, type: string, inApp: boolean) =>
  send<NotificationSettings>("PATCH", "/notification-preferences", { organization_id: organizationId, in_app: { [type]: inApp } });

export interface NotificationSettingsUpdate {
  expected_version: number;
  types: Record<string, { in_app: boolean; email: boolean; webhook: boolean }>;
  email_recipients: string[];
  confirm: boolean;
}

export const updateNotificationSettings = (organizationId: number, update: NotificationSettingsUpdate) =>
  send<NotificationSettings>("PUT", `${org(organizationId)}/notification-settings`, update);
export const addWebhook = (organizationId: number, url: string) =>
  send<CreatedWebhook>("POST", `${org(organizationId)}/notification-webhooks`, { url, confirm: true });
export const removeWebhook = (organizationId: number, endpointId: string) =>
  send<{ removed: boolean }>("DELETE", `${org(organizationId)}/notification-webhooks/${encodeURIComponent(endpointId)}`);
export const listDeliveries = (organizationId: number, cursor?: string | null): Promise<Page<NotificationDelivery>> =>
  getPage<NotificationDelivery>(`${org(organizationId)}/notification-deliveries`, { cursor, limit: 10 });
