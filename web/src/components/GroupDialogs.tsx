import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { archiveGroup, updateGroup } from "../api/groups";
import type { RepositoryGroup } from "../api/types";
import { plural } from "../lib/format";
import { ActionError } from "./ActionError";
import { ConfirmDialog } from "./ConfirmDialog";
import { Notice } from "./Primitives";

export function RenameGroupDialog({ group, onClose }: { group: RepositoryGroup; onClose: (renamed?: RepositoryGroup) => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description ?? "");
  const rename = useMutation({
    mutationFn: () => updateGroup(group.id, { name: name.trim(), description: description.trim() }),
    onSuccess: (updated) => {
      void queryClient.invalidateQueries({ queryKey: ["governance", group.organization_id] });
      void queryClient.invalidateQueries({ queryKey: ["governance", "group", group.id] });
      onClose(updated);
    },
  });
  const unchanged = name.trim() === group.name && description.trim() === (group.description ?? "");
  return (
    <ConfirmDialog
      open
      tone="default"
      title="Rename group"
      confirmLabel="Save"
      onCancel={() => onClose()}
      onConfirm={() => rename.mutate()}
      confirmDisabled={!name.trim() || unchanged}
      busy={rename.isPending}
    >
      <div className="field">
        <label htmlFor="rename-group-name">Group name</label>
        <input id="rename-group-name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="rename-group-description">Description</label>
        <input id="rename-group-description" maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <p className="muted small">Policy versions, exceptions and audit events refer to the group by its ID, so they keep pointing to it.</p>
      <ActionError error={rename.error} fallback="The group could not be renamed." />
    </ConfirmDialog>
  );
}

/** Groups are archived, never deleted. Archiving one with a policy or exceptions changes effective policy and must be confirmed. */
export function ArchiveGroupDialog({ group, onClose }: { group: RepositoryGroup; onClose: (archived?: boolean) => void }) {
  const queryClient = useQueryClient();
  const [understood, setUnderstood] = useState(false);
  const affectsPolicy = group.policy_version > 0 || group.active_exceptions > 0;
  const archive = useMutation({
    mutationFn: () => archiveGroup(group.id, affectsPolicy),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["governance", group.organization_id] });
      void queryClient.invalidateQueries({ queryKey: ["governance", "group", group.id] });
      onClose(true);
    },
  });
  return (
    <ConfirmDialog
      open
      title={`Archive ${group.name}?`}
      confirmLabel="Archive group"
      onCancel={() => onClose()}
      onConfirm={() => archive.mutate()}
      confirmDisabled={affectsPolicy && !understood}
      busy={archive.isPending}
    >
      <p>An archived group is kept for history and can no longer be changed. Its repositories stay in CommitGuard.</p>
      {affectsPolicy ? (
        <>
          <Notice tone="warning" title="This changes the effective policy of its repositories">
            The group has {group.policy_version ? `policy v${group.policy_version}` : "no published policy"} and {plural(group.active_exceptions, "active exception")}. The policy stops applying to its {plural(group.repository_count, "repository", "repositories")}, and its exceptions end: requests are cancelled and active exceptions revoked, with the history kept.
          </Notice>
          <label className="checkbox">
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand the effective policy of these repositories changes.
          </label>
        </>
      ) : null}
      <ActionError error={archive.error} fallback="The group could not be archived." />
    </ConfirmDialog>
  );
}
