import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, FilePen, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { getSecurityExceptions } from "../api/governance";
import { addGroupMembers, getGroup, removeGroupMembers } from "../api/groups";
import { listTargetVersions } from "../api/policyWorkflow";
import type { GroupMember, Permission, RepositoryGroupDetail } from "../api/types";
import { useSession } from "../auth/session";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ArchiveGroupDialog, RenameGroupDialog } from "../components/GroupDialogs";
import { ScopedRulesTable, StrengthExplanation, TargetVersionHistory } from "../components/PolicyDisplay";
import { KeyValueList, Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { RepositoryPicker } from "../components/RepositoryPicker";
import { EmptyState, ErrorState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { count, plural } from "../lib/format";
import { EXCEPTION_STATUS, POLICY_ACTION } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

const HEX_ID = /^[0-9a-f]{32}$/;

function Members({ detail }: { detail: RepositoryGroupDetail }) {
  const queryClient = useQueryClient();
  const group = detail.group;
  const [adding, setAdding] = useState(false);
  const [selection, setSelection] = useState<Map<number, string>>(new Map());
  const [removing, setRemoving] = useState<GroupMember | null>(null);
  const onSaved = (updated: RepositoryGroupDetail) => {
    queryClient.setQueryData(["governance", "group", group.id], updated);
    void queryClient.invalidateQueries({ queryKey: ["governance", group.organization_id] });
  };
  const add = useMutation({
    mutationFn: () => addGroupMembers(group.id, [...selection.keys()]),
    onSuccess: (updated) => {
      onSaved(updated);
      setAdding(false);
      setSelection(new Map());
    },
  });
  const remove = useMutation({
    mutationFn: (member: GroupMember) => removeGroupMembers(group.id, [member.repository_id]),
    onSuccess: (updated) => {
      onSaved(updated);
      setRemoving(null);
    },
  });
  const manage = detail.can_manage && !group.archived_at;
  return (
    <>
      {detail.hidden_repositories > 0 ? (
        <p className="panel__inset muted small">
          {plural(detail.hidden_repositories, "member is", "members are")} not shown because GitHub did not report {detail.hidden_repositories === 1 ? "it" : "them"} to your session. The group&apos;s policy still applies to {detail.hidden_repositories === 1 ? "it" : "them"}.
        </p>
      ) : null}
      {detail.repositories.length ? (
        <div className="table-wrap">
          <table className="table">
            <caption className="visually-hidden">Repositories in {group.name}</caption>
            <thead>
              <tr><th scope="col">Repository</th><th scope="col">Added</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr>
            </thead>
            <tbody>
              {detail.repositories.map((member) => (
                <tr key={member.repository_id}>
                  <td data-label="Repository" className="table__primary">
                    <Link to={routes.repository(member.repository_id)} className="strong break">{member.full_name}</Link>
                  </td>
                  <td data-label="Added" className="small">{member.added_by ?? "unknown"} · <Time value={member.added_at} /></td>
                  <td data-label="Actions" className="align-end">
                    {manage ? (
                      <button type="button" className="button button--ghost" onClick={() => setRemoving(member)}>
                        <Trash2 size={14} aria-hidden="true" /> Remove<span className="visually-hidden"> {member.full_name}</span>
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No repositories in this group." />
      )}
      {manage ? (
        <div className="panel__inset panel__footer">
          <button type="button" className="button button--secondary" onClick={() => setAdding(true)}>
            <Plus size={14} aria-hidden="true" /> Add repositories
          </button>
        </div>
      ) : null}
      <ActionError error={remove.error} fallback="The repository could not be removed." />
      {adding ? (
        <ConfirmDialog
          open
          tone="default"
          title={`Add repositories to ${group.name}`}
          confirmLabel={`Add ${plural(selection.size, "repository", "repositories")}`}
          onCancel={() => {
            setAdding(false);
            add.reset();
          }}
          onConfirm={() => add.mutate()}
          confirmDisabled={selection.size === 0}
          busy={add.isPending}
        >
          <RepositoryPicker
            organization={group.organization_id}
            legend="Repositories to add"
            selected={selection}
            onChange={setSelection}
            exclude={new Set(detail.repositories.map((m) => m.repository_id))}
          />
          {group.policy_version ? <p className="muted small">Policy v{group.policy_version} of this group applies to the repositories you add.</p> : null}
          <ActionError error={add.error} fallback="The repositories could not be added." />
        </ConfirmDialog>
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        title={`Remove ${removing?.full_name ?? "repository"}?`}
        confirmLabel="Remove from group"
        onCancel={() => setRemoving(null)}
        onConfirm={() => removing && remove.mutate(removing)}
        busy={remove.isPending}
      >
        <p>The repository stays in CommitGuard. {group.policy_version ? `Policy v${group.policy_version} of this group and its group exceptions stop applying to it.` : "Group exceptions stop applying to it."}</p>
      </ConfirmDialog>
    </>
  );
}

function GroupPolicy({ detail, can }: { detail: RepositoryGroupDetail; can: (permission: Permission) => boolean }) {
  const group = detail.group;
  const query = useQuery({
    queryKey: ["governance", group.organization_id, "target-versions", "group", group.id, null],
    queryFn: () => listTargetVersions(group.organization_id, "group", group.id, null),
  });
  return (
    <>
      <QueryBoundary query={query} errorTitle="We could not load the group policy." loading={<SkeletonRows rows={3} />}>
        {(data) => (
          <>
            <p className="muted small">
              {data.policy.version
                ? <>Version {data.policy.version} · published <Time value={data.policy.updated_at} /> by {data.policy.updated_by?.login ?? "unknown"}{data.policy.reason ? ` · “${data.policy.reason}”` : ""}</>
                : "Nothing is published for this group yet: organization policy and each repository decide."}
            </p>
            {"target" in data.policy ? <ScopedRulesTable rules={data.policy.rules} caption={`Policy of ${group.name}`} /> : null}
          </>
        )}
      </QueryBoundary>
      <details className="disclosure">
        <summary>Mandatory and default requirements</summary>
        <StrengthExplanation />
      </details>
      <details className="disclosure">
        <summary>Version history</summary>
        <TargetVersionHistory organization={group.organization_id} type="group" targetId={group.id} canRollback={can("policies:rollback")} />
      </details>
    </>
  );
}

function GroupExceptions({ detail }: { detail: RepositoryGroupDetail }) {
  const group = detail.group;
  const query = useQuery({ queryKey: ["governance", group.organization_id, "exceptions-summary"], queryFn: () => getSecurityExceptions(group.organization_id) });
  return (
    <QueryBoundary query={query} errorTitle="We could not load exceptions." loading={<SkeletonRows rows={2} />}>
      {(data) => {
        const items = data.exceptions.filter((e) => e.scope.type === "group" && e.scope.id === group.id);
        if (items.length === 0) return <EmptyState title="No exceptions for this group." />;
        return (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Exceptions for {group.name}</caption>
              <thead>
                <tr><th scope="col">Rule</th><th scope="col">Action</th><th scope="col">Status</th><th scope="col">Expires</th></tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.id} className="table__row--link">
                    <td data-label="Rule" className="table__primary">
                      <Link to={routes.exception(e.id)} className="row-link">
                        <span className="stack"><span className="strong">{e.rule_name}</span><code className="muted">{e.rule_id}</code></span>
                      </Link>
                    </td>
                    <td data-label="Action"><Badge map={POLICY_ACTION} value={e.action} compact /></td>
                    <td data-label="Status">
                      <Badge map={EXCEPTION_STATUS} value={e.status} compact />
                      {e.expiring_soon ? <span className="tag tag--warning">Expiring soon</span> : null}
                    </td>
                    <td data-label="Expires">{e.permanent ? "Permanent" : <Time value={e.expires_at} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }}
    </QueryBoundary>
  );
}

export default function RepositoryGroup() {
  const { groupId = "" } = useParams();
  const valid = HEX_ID.test(groupId);
  const { organizations } = useSession();
  const query = useQuery({ queryKey: ["governance", "group", groupId], queryFn: () => getGroup(groupId), enabled: valid });
  useDocumentTitle(query.data ? `Group ${query.data.group.name}` : "Repository group");
  const [renaming, setRenaming] = useState(false);
  const [archiving, setArchiving] = useState(false);

  if (!valid) return <NotFoundContent resource="group" />;
  if (query.isPending) return <SkeletonRows rows={8} label="Loading group…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="group" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this group." error={query.error} onRetry={() => void query.refetch()} />;

  const detail = query.data;
  const group = detail.group;
  const access = organizations.find((o) => o.organization.id === group.organization_id);
  const can = (permission: Permission) => Boolean(access?.permissions.includes(permission));
  const editable = detail.can_manage && !group.archived_at;
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.organizationRepositories}>Repository matrix</Link>}
        title={group.name}
        description={group.description ?? "Repository group"}
        actions={
          <>
            {can("policies:write") && !group.archived_at ? (
              <Link className="button button--primary" to={routes.newDraft({ type: "group", id: group.id })}>
                <FilePen size={14} aria-hidden="true" /> Propose policy change
              </Link>
            ) : null}
            {editable ? (
              <>
                <button type="button" className="button button--secondary" onClick={() => setRenaming(true)}>
                  <Pencil size={14} aria-hidden="true" /> Rename
                </button>
                <button type="button" className="button button--danger-outline" onClick={() => setArchiving(true)}>
                  <Archive size={14} aria-hidden="true" /> Archive
                </button>
              </>
            ) : null}
          </>
        }
      />
      {group.archived_at ? (
        <Notice tone="warning" title="Archived group">
          Archived <Time value={group.archived_at} />. Its policy no longer applies and it cannot be changed; it is kept for history.
        </Notice>
      ) : null}
      <div className="grid-2">
        <Panel title="Summary" id="summary">
          <KeyValueList
            items={[
              ["Repositories", <Link to={`${routes.organizationRepositories}?group=${encodeURIComponent(group.id)}`}>{plural(group.repository_count, "repository", "repositories")} in the matrix</Link>],
              ["Policy", group.policy_version ? `v${group.policy_version}` : <span className="muted">No published policy</span>],
              ["Active exceptions", count(group.active_exceptions)],
              ["Created", <>{group.created_by ?? "unknown"} · <Time value={group.created_at} /></>],
              ["Updated", <Time value={group.updated_at} />],
            ]}
          />
        </Panel>
        <Panel title="Group policy" id="group-policy">
          <GroupPolicy detail={detail} can={can} />
        </Panel>
      </div>
      <Panel title="Members" id="members" flush>
        <Members detail={detail} />
      </Panel>
      {can("exceptions:read") ? (
        <Panel
          title="Group exceptions"
          id="exceptions"
          flush
          actions={can("exceptions:create") && !group.archived_at ? <Link to={`${routes.newException}?scope=group&id=${encodeURIComponent(group.id)}`}>Request an exception</Link> : null}
        >
          <GroupExceptions detail={detail} />
        </Panel>
      ) : null}
      {renaming ? <RenameGroupDialog group={group} onClose={() => setRenaming(false)} /> : null}
      {archiving ? <ArchiveGroupDialog group={group} onClose={() => setArchiving(false)} /> : null}
    </>
  );
}
