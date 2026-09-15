import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { listSessions, revokeSession } from "../api/auth";
import { ApiError } from "../api/client";
import { listMembers, removeMember, setMemberRole } from "../api/organizations";
import type { OrganizationAccess, Role } from "../api/types";
import { useSession } from "../auth/session";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { KeyValueList, Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { ROLE_LABEL } from "../lib/labels";
import { applyTheme, readTheme, writeTheme, type ThemePreference } from "../lib/preferences";

const ROLES: Role[] = ["viewer", "security_manager", "admin", "owner"];

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

function Members({ access, userId }: { access: OrganizationAccess; userId: number }) {
  const queryClient = useQueryClient();
  const organization = access.organization.id;
  const canManage = access.permissions.includes("members:manage");
  const query = useQuery({ queryKey: ["members", organization], queryFn: () => listMembers(organization) });
  const [newUser, setNewUser] = useState("");
  const [newLogin, setNewLogin] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");
  const [removing, setRemoving] = useState<number | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["members", organization] });
  const change = useMutation({ mutationFn: ({ user, role, login }: { user: number; role: Role; login?: string }) => setMemberRole(organization, user, role, login), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (user: number) => removeMember(organization, user), onSuccess: () => { setRemoving(null); refresh(); } });
  const error = change.error ?? remove.error;
  return (
    <>
      {error ? <Notice tone="danger">{error instanceof ApiError ? error.message : "The change could not be saved."}</Notice> : null}
      <QueryBoundary query={query} errorTitle="We could not load members." loading={<SkeletonRows rows={3} />}>
        {(page) => (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Members of {access.organization.login}</caption>
              <thead><tr><th scope="col">Member</th><th scope="col">GitHub user ID</th><th scope="col">Role</th><th scope="col">Granted by</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
              <tbody>
                {page.items.map((m) => {
                  const editable = canManage && !m.implicit && m.user_id !== userId;
                  return (
                    <tr key={m.user_id}>
                      <td data-label="Member" className="table__primary">{m.login ?? "Not signed in yet"}{m.user_id === userId ? <span className="tag">You</span> : null}</td>
                      <td data-label="GitHub user ID"><code>{m.user_id}</code></td>
                      <td data-label="Role">
                        {editable ? (
                          <>
                            <label className="visually-hidden" htmlFor={`role-${m.user_id}`}>Role for {m.login ?? m.user_id}</label>
                            <select id={`role-${m.user_id}`} value={m.role} onChange={(e) => change.mutate({ user: m.user_id, role: e.target.value as Role })}>
                              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                            </select>
                          </>
                        ) : (
                          `${ROLE_LABEL[m.role]}${m.implicit ? " (account owner)" : ""}`
                        )}
                      </td>
                      <td data-label="Granted by">{m.granted_by}</td>
                      <td data-label="Actions" className="align-end">{editable ? <button type="button" className="button button--ghost" onClick={() => setRemoving(m.user_id)}>Remove</button> : null}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </QueryBoundary>
      {canManage ? (
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            const id = Number(newUser);
            if (!Number.isSafeInteger(id) || id <= 0) return;
            change.mutate({ user: id, role: newRole, login: newLogin.trim() || undefined }, { onSuccess: () => { setNewUser(""); setNewLogin(""); refresh(); } });
          }}
        >
          <div className="field">
            <label htmlFor={`new-user-${organization}`}>GitHub user ID</label>
            <input id={`new-user-${organization}`} inputMode="numeric" pattern="[0-9]{1,16}" required value={newUser} onChange={(e) => setNewUser(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`new-login-${organization}`}>Login (for display)</label>
            <input id={`new-login-${organization}`} maxLength={39} value={newLogin} onChange={(e) => setNewLogin(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`new-role-${organization}`}>Role</label>
            <select id={`new-role-${organization}`} value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
            </select>
          </div>
          <button type="submit" className="button button--primary" disabled={change.isPending}>Grant role</button>
          <p className="muted small field--full">Members are identified by numeric GitHub user ID because logins can be renamed. Find an ID with <code>gh api users/LOGIN --jq .id</code>.</p>
        </form>
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        title="Remove this member?"
        confirmLabel="Remove member"
        onCancel={() => setRemoving(null)}
        onConfirm={() => removing !== null && remove.mutate(removing)}
        busy={remove.isPending}
      >
        <p>They lose access to {access.organization.login} in CommitGuard on their next request. Their GitHub access is not changed.</p>
      </ConfirmDialog>
    </>
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
      <PageHeader title="Settings" description="Your account, access, sessions and display preferences." />
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
              </li>
            ))}
          </ul>
        )}
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
