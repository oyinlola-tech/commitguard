import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, BellOff, CheckCheck, MailOpen, Mail } from "lucide-react";
import { Link } from "react-router";

import { ApiError } from "../api/client";
import {
  archiveNotification,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  markNotificationUnread,
} from "../api/notifications";
import type { NotificationItem } from "../api/types";
import { useSession } from "../auth/session";
import { Badge } from "../components/Badge";
import { Pagination, useCursorPager } from "../components/Pagination";
import { Notice, PageHeader, Time } from "../components/Primitives";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useUrlState } from "../hooks/useUrlState";
import { count } from "../lib/format";
import { SEVERITY } from "../lib/labels";
import { internalLink, routes } from "../lib/routes";

export const NOTIFICATIONS_QUERY_KEY = ["notifications"] as const;

const TABS = [
  { key: "", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "critical", label: "Critical" },
  { key: "violations", label: "Violations" },
  { key: "policy", label: "Policy" },
  { key: "github", label: "GitHub" },
  { key: "scans", label: "Scans" },
  { key: "archived", label: "Archived" },
] as const;

const KEYS = ["tab"] as const;

const EMPTY_TEXT: Record<string, string> = {
  "": "No notifications",
  unread: "No unread notifications",
  critical: "No critical notifications",
  archived: "No archived notifications",
};

function NotificationRow({ item }: { item: NotificationItem }) {
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
  const read = useMutation({ mutationFn: () => markNotificationRead(item.id), onSuccess: refresh });
  const unread = useMutation({ mutationFn: () => markNotificationUnread(item.id), onSuccess: refresh });
  const archive = useMutation({ mutationFn: () => archiveNotification(item.id), onSuccess: refresh });
  const link = internalLink(item.link);
  const error = read.error ?? unread.error ?? archive.error;
  const busy = read.isPending || unread.isPending || archive.isPending;
  return (
    <li className={`notification notification--${item.state} notification--${item.severity}`}>
      <div className="notification__marker" aria-hidden="true" />
      <div className="notification__main">
        <div className="notification__head">
          <Badge map={SEVERITY} value={item.severity} compact />
          {item.state === "unread" ? <span className="visually-hidden">Unread. </span> : null}
          {link ? (
            <Link to={link} className="notification__title" onClick={() => item.state === "unread" && read.mutate()}>
              {item.title}
            </Link>
          ) : (
            <span className="notification__title">{item.title}</span>
          )}
        </div>
        <p className="notification__body">{item.body}</p>
        <p className="notification__meta muted small">
          <Time value={item.last_occurred_at} />
          {item.repository ? (
            <>
              {" · "}
              <Link to={routes.repository(item.repository.id)}>{item.repository.full_name}</Link>
            </>
          ) : null}
          {item.occurrences > 1 ? ` · ${count(item.occurrences)} occurrences` : ""}
        </p>
        {error ? <Notice tone="danger">{error instanceof ApiError ? error.message : "The notification could not be updated."}</Notice> : null}
      </div>
      <div className="notification__actions">
        {item.state === "unread" ? (
          <button type="button" className="icon-button" onClick={() => read.mutate()} disabled={busy} aria-label={`Mark as read: ${item.title}`} title="Mark as read">
            <MailOpen size={16} aria-hidden="true" />
          </button>
        ) : item.state === "read" ? (
          <button type="button" className="icon-button" onClick={() => unread.mutate()} disabled={busy} aria-label={`Mark as unread: ${item.title}`} title="Mark as unread">
            <Mail size={16} aria-hidden="true" />
          </button>
        ) : null}
        {item.state !== "archived" ? (
          <button type="button" className="icon-button" onClick={() => archive.mutate()} disabled={busy} aria-label={`Archive: ${item.title}`} title="Archive">
            <Archive size={16} aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </li>
  );
}

export default function Notifications() {
  useDocumentTitle("Notifications");
  const { organization } = useSession();
  const queryClient = useQueryClient();
  const { values, cursor, update } = useUrlState(KEYS);
  const tab = TABS.some((t) => t.key === values.tab) ? values.tab : "";
  const state = tab === "unread" ? "unread" : tab === "archived" ? "archived" : undefined;
  const category = ["critical", "violations", "policy", "github", "scans"].includes(tab) ? tab : undefined;
  const pager = useCursorPager(`${tab}-${organization}`, cursor, (next) => update({ cursor: next }, { resetCursor: false }));
  const query = useQuery({
    queryKey: [...NOTIFICATIONS_QUERY_KEY, "list", tab, organization, cursor],
    queryFn: () => listNotifications({ state, category, organization, cursor }),
    refetchInterval: 30_000,
  });
  const readAll = useMutation({
    mutationFn: () => markAllNotificationsRead(organization),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY }),
  });
  const unread = query.data?.counts?.unread ?? 0;

  return (
    <>
      <PageHeader
        title="Notifications"
        description="Security events that need attention: blocked commits, policy changes and rollbacks, GitHub installations and merge queue failures."
        actions={
          <button type="button" className="button button--secondary" onClick={() => readAll.mutate()} disabled={readAll.isPending || unread === 0}>
            <CheckCheck size={14} aria-hidden="true" /> Mark all as read
          </button>
        }
      />
      {readAll.data ? <Notice tone="success">{count(readAll.data.data.updated)} marked as read.</Notice> : null}
      <div className="tabs" role="group" aria-label="Filter notifications">
        {TABS.map((t) => (
          <button
            key={t.key || "all"}
            type="button"
            className={`tabs__item${tab === t.key ? " tabs__item--active" : ""}`}
            aria-pressed={tab === t.key}
            onClick={() => update({ tab: t.key || null })}
          >
            {t.label}
            {t.key === "unread" && unread > 0 ? (
              <span className="tabs__count">{query.data?.counts?.capped ? "999+" : count(unread)}</span>
            ) : null}
          </button>
        ))}
      </div>
      <QueryBoundary
        query={query}
        errorTitle="We could not load notifications."
        loading={<SkeletonRows rows={5} label="Loading notifications…" />}
        isEmpty={(page) => page.items.length === 0}
        empty={
          <EmptyState title={EMPTY_TEXT[tab] ?? "No notifications in this category"} icon={<BellOff size={20} />}>
            CommitGuard notifies you when a scan blocks commits, when organization policy changes or is rolled back, and when GitHub enforcement is at risk.
          </EmptyState>
        }
      >
        {(page) => (
          <>
            <ul className="notifications" aria-label="Notifications">
              {page.items.map((item) => (
                <NotificationRow key={item.id} item={item} />
              ))}
            </ul>
            <Pagination page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} label="Notifications" />
          </>
        )}
      </QueryBoundary>
    </>
  );
}
