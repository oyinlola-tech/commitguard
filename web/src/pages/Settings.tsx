import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { listSessions, revokeSession } from "../api/auth";
import { listNotificationPreferences } from "../api/notifications";
import { useSession } from "../auth/session";
import { Members } from "../components/Members";
import { NotificationSettingsPanel } from "../components/NotificationSettingsPanel";
import { KeyValueList, PageHeader, Panel, Time } from "../components/Primitives";
import { QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { ROLE_LABEL } from "../lib/labels";
import { applyTheme, readTheme, writeTheme, type ThemePreference } from "../lib/preferences";
import { routes } from "../lib/routes";

function Sessions() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["sessions"], queryFn: listSessions });
  const revoke = useMutation({
    mutationFn: (id: string) => revokeSession(id),
    onSuccess: (_, id) => {
      const current = query.data?.find((s) => s.id === id)?.current;
      if (current) window.location.assign("/login?reason=signed_out");
      else void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });
  return (
    <QueryBoundary query={query} errorTitle="We could not load your sessions." loading={<SkeletonRows rows={2} />}>
      {(sessions) => (
        <ul className="sessions">
          {sessions.map((s) => (
            <li key={s.id} className="sessions__item">
              <div>
                <p className="strong">{s.user_agent}{s.current ? <span className="tag">This browser</span> : null}</p>
                <p className="muted small">
                  Signed in <Time value={s.created_at} /> · last active <Time value={s.last_seen_at} /> · expires <Time value={s.expires_at} />
                </p>
              </div>
              <button type="button" className="button button--secondary" onClick={() => revoke.mutate(s.id)} disabled={revoke.isPending}>
                {s.current ? "Sign out" : "Revoke"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </QueryBoundary>
  );
}

function Notifications() {
  const query = useQuery({ queryKey: ["notification-preferences"], queryFn: listNotificationPreferences });
  return (
    <QueryBoundary query={query} errorTitle="We could not load notification settings." loading={<SkeletonRows rows={3} />} isEmpty={(d) => d.length === 0} empty={<p className="muted">No organization notifications are available to your account.</p>}>
      {(organizations) => (
        <div className="stack-lg">
          {organizations.map((settings) => (
            <section key={`${settings.organization.id}-${settings.version}`} aria-labelledby={`notifications-${settings.organization.id}`}>
              <h3 className="subheading" id={`notifications-${settings.organization.id}`}>{settings.organization.login}</h3>
              <NotificationSettingsPanel settings={settings} />
            </section>
          ))}
        </div>
      )}
    </QueryBoundary>
  );
}

function Appearance() {
  const [theme, setTheme] = useState<ThemePreference>(readTheme);
  return (
    <fieldset className="radio-group">
      <legend>Theme</legend>
      {(["system", "light", "dark"] as ThemePreference[]).map((value) => (
        <label key={value} className="radio">
          <input type="radio" name="theme" value={value} checked={theme === value} onChange={() => { writeTheme(value); applyTheme(value); setTheme(value); }} />
          {value === "system" ? "Match my system" : value === "light" ? "Light" : "Dark"}
        </label>
      ))}
    </fieldset>
  );
}

export default function Settings() {
  useDocumentTitle("Settings");
  const { session, organizations } = useSession();
  return (
    <>
      <PageHeader title="Settings" description="Your account, access, notifications, sessions and display preferences." />
      <div className="grid-2">
        <Panel title="Profile" id="profile">
          <KeyValueList items={[["GitHub login", session.user.login], ["GitHub user ID", <code>{session.user.id}</code>], ["Session expires", <Time value={session.session.expires_at} absolute />]]} />
          <p className="muted small">Your identity comes from GitHub. CommitGuard stores no password and keeps no GitHub token after sign-in.</p>
        </Panel>
        <Panel title="Appearance" id="appearance">
          <Appearance />
        </Panel>
      </div>
      <Panel title="Organizations and roles" id="organizations">
        {organizations.length === 0 ? (
          <p className="muted">You have no role in an organization. Ask an owner to grant one using your GitHub user ID ({session.user.id}).</p>
        ) : (
          <ul className="access">
            {organizations.map((o) => (
              <li key={o.organization.id} className="access__item">
                <p className="strong">{o.organization.login} <span className="tag">{ROLE_LABEL[o.role]}{o.implicit_role ? " · account owner" : ""}</span></p>
                <p className="muted small">{o.permissions.join(" · ")}</p>
                {o.permissions.includes("organization:read") ? (
                  <p className="small">
                    <Link to={routes.organizationSettings}>Organization settings</Link>
                    {o.permissions.includes("members:read") ? <> · <Link to={routes.organizationMembers}>Members</Link></> : null}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Notifications" id="notifications">
        <Notifications />
      </Panel>
      <Panel title="Security · active sessions" id="sessions">
        <Sessions />
      </Panel>
      {organizations.filter((o) => o.permissions.includes("members:read")).map((o) => (
        <Panel key={o.organization.id} title={`Members · ${o.organization.login}`} id={`members-${o.organization.id}`} flush>
          <Members access={o} userId={session.user.id} />
        </Panel>
      ))}
    </>
  );
}
