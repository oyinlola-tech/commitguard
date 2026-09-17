import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../api/client";
import { listMembers, removeMember, setMemberRole } from "../api/organizations";
import type { OrganizationAccess, Role } from "../api/types";
import { ROLE_LABEL } from "../lib/labels";
import { ConfirmDialog } from "./ConfirmDialog";
import { Notice } from "./Primitives";
import { QueryBoundary, SkeletonRows } from "./States";

const ROLES: Role[] = ["viewer", "security_manager", "admin", "owner"];

/** Organization members and roles. Every change is authorized and audited on the server. */
export function Members({ access, userId }: { access: OrganizationAccess; userId: number }) {
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
