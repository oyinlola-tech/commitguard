import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { ApiError } from "../api/client";
import {
  addWebhook,
  listDeliveries,
  removeWebhook,
  setPersonalPreference,
  updateNotificationSettings,
} from "../api/notifications";
import type { CreatedWebhook, NotificationSettings } from "../api/types";
import { DELIVERY_STATUS } from "../lib/labels";
import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { Notice, Time } from "./Primitives";
import { ReauthenticateNotice } from "./Reauthenticate";
import { QueryBoundary, SkeletonRows } from "./States";

type Channels = { in_app: boolean; email: boolean; webhook: boolean };

const CHANNEL_LABEL: Record<keyof Channels, string> = { in_app: "In-app", email: "E-mail", webhook: "Webhook" };

function errorNotice(error: unknown) {
  if (!error) return null;
  if (error instanceof ApiError && error.code === "REAUTHENTICATION_REQUIRED") return <ReauthenticateNotice />;
  return <Notice tone="danger">{error instanceof ApiError ? error.message : "The change could not be saved."}</Notice>;
}

function Deliveries({ organization }: { organization: number }) {
  const query = useQuery({ queryKey: ["notification-deliveries", organization], queryFn: () => listDeliveries(organization), refetchInterval: 30_000 });
  return (
    <QueryBoundary query={query} errorTitle="We could not load deliveries." loading={<SkeletonRows rows={2} />} isEmpty={(p) => p.items.length === 0} empty={<p className="muted small">No e-mail or webhook deliveries yet.</p>}>
      {(page) => (
        <div className="table-wrap">
          <table className="table">
            <caption className="visually-hidden">Recent notification deliveries</caption>
            <thead>
              <tr><th scope="col">Notification</th><th scope="col">Channel</th><th scope="col">Status</th><th scope="col">Attempts</th><th scope="col">Last attempt</th></tr>
            </thead>
            <tbody>
              {page.items.map((d) => (
                <tr key={d.id}>
                  <td data-label="Notification" className="table__primary">{d.title}</td>
                  <td data-label="Channel">{d.channel === "email" ? "E-mail" : "Webhook"}</td>
                  <td data-label="Status">
                    <Badge map={DELIVERY_STATUS} value={d.status} compact />
                    {d.failure_code ? <code className="muted small"> {d.failure_code}</code> : null}
                  </td>
                  <td data-label="Attempts">{d.attempts}{d.status === "pending" && d.next_retry_at ? <span className="muted small"> · retry <Time value={d.next_retry_at} /></span> : null}</td>
                  <td data-label="Last attempt"><Time value={d.last_attempt_at} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryBoundary>
  );
}

function Webhooks({ settings }: { settings: NotificationSettings }) {
  const queryClient = useQueryClient();
  const organization = settings.organization.id;
  const [url, setUrl] = useState("");
  const [confirmAdd, setConfirmAdd] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedWebhook | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["notification-preferences"] });
  const add = useMutation({
    mutationFn: () => addWebhook(organization, url.trim()),
    onSuccess: (result) => {
      setCreated(result.data);
      setUrl("");
      setConfirmAdd(false);
      refresh();
    },
    onError: () => setConfirmAdd(false),
  });
  const remove = useMutation({ mutationFn: (id: string) => removeWebhook(organization, id), onSuccess: () => { setRemoving(null); refresh(); } });
  if (!settings.channels.webhook) {
    return <p className="muted small">Webhook delivery is not configured on this CommitGuard server (<code>COMMITGUARD_NOTIFICATION_SIGNING_KEY</code>).</p>;
  }
  return (
    <>
      {created ? (
        <Notice tone="success" title="Webhook added: copy the signing secret now">
          <p>CommitGuard shows this secret only once and does not store it. Use it to verify <code>X-CommitGuard-Signature</code>.</p>
          <p><code className="break secret" data-testid="webhook-secret">{created.signing_secret}</code></p>
          <button type="button" className="button button--secondary" onClick={() => setCreated(null)}>I saved the secret</button>
        </Notice>
      ) : null}
      {errorNotice(add.error ?? remove.error)}
      {settings.webhooks.length ? (
        <ul className="webhooks">
          {settings.webhooks.map((w) => (
            <li key={w.id} className="webhooks__item">
              <code className="break">{w.url}</code>
              <span className="muted small">added <Time value={w.created_at} />{w.created_by ? ` by ${w.created_by}` : ""}</span>
              <button type="button" className="button button--ghost" onClick={() => setRemoving(w.id)} aria-label={`Remove webhook ${w.url}`}>
                <Trash2 size={14} aria-hidden="true" /> Remove
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">No webhook endpoints.</p>
      )}
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (url.trim()) setConfirmAdd(true);
        }}
      >
        <div className="field field--grow">
          <label htmlFor={`webhook-url-${organization}`}>HTTPS endpoint URL</label>
          <input id={`webhook-url-${organization}`} type="url" inputMode="url" required maxLength={2048} placeholder="https://hooks.example.com/commitguard" value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>
        <button type="submit" className="button button--secondary"><Plus size={14} aria-hidden="true" /> Add webhook</button>
      </form>
      <ConfirmDialog
        open={confirmAdd}
        title="Send security notifications to this endpoint?"
        confirmLabel="Add webhook"
        tone="default"
        onCancel={() => setConfirmAdd(false)}
        onConfirm={() => add.mutate()}
        busy={add.isPending}
      >
        <p>Notifications about <strong>{settings.organization.login}</strong> - repository names, rules and policy changes - will be sent to:</p>
        <p><code className="break">{url}</code></p>
        <p className="muted small">Requests are signed and time-stamped. Adding an endpoint requires a sign-in from the last 15 minutes.</p>
      </ConfirmDialog>
      <ConfirmDialog
        open={removing !== null}
        title="Remove this webhook?"
        confirmLabel="Remove webhook"
        onCancel={() => setRemoving(null)}
        onConfirm={() => removing && remove.mutate(removing)}
        busy={remove.isPending}
      >
        <p>Pending deliveries to this endpoint are cancelled. In-app notifications and e-mail are not affected.</p>
      </ConfirmDialog>
    </>
  );
}

export function NotificationSettingsPanel({ settings }: { settings: NotificationSettings }) {
  const queryClient = useQueryClient();
  const organization = settings.organization.id;
  const original = useMemo(() => Object.fromEntries(settings.types.map((t) => [t.type, { ...t.organization }])) as Record<string, Channels>, [settings]);
  const [draft, setDraft] = useState<Record<string, Channels>>(original);
  const [recipients, setRecipients] = useState(settings.email_recipients.join("\n"));
  const [pendingOff, setPendingOff] = useState<string[] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["notification-preferences"] });

  const personal = useMutation({ mutationFn: ({ type, value }: { type: string; value: boolean }) => setPersonalPreference(organization, type, value), onSuccess: refresh });
  const recipientList = recipients.split(/[\n,]/).map((r) => r.trim()).filter(Boolean);
  const save = useMutation({
    mutationFn: (confirm: boolean) => updateNotificationSettings(organization, { expected_version: settings.version, types: draft, email_recipients: recipientList, confirm }),
    onSuccess: (result) => {
      setPendingOff(null);
      setSaved(`Saved as version ${result.data.version}.`);
      refresh();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "CONFIRMATION_REQUIRED") setPendingOff([error.message]);
    },
  });
  const dirty = JSON.stringify(draft) !== JSON.stringify(original) || recipientList.join("\n") !== settings.email_recipients.join("\n");
  const turnedOff = settings.types.flatMap((t) =>
    (Object.keys(CHANNEL_LABEL) as (keyof Channels)[])
      .filter((channel) => original[t.type]?.[channel] && !draft[t.type]?.[channel])
      .map((channel) => `${t.label}: ${CHANNEL_LABEL[channel]}`),
  );
  const removedRecipients = settings.email_recipients.filter((r) => !recipientList.includes(r));

  const setChannel = (type: string, channel: keyof Channels, value: boolean) =>
    setDraft((current) => ({ ...current, [type]: { ...(current[type] ?? { in_app: true, email: false, webhook: false }), [channel]: value } }));

  return (
    <div className="stack">
      <p className="muted small">
        Delivery: in-app always
        {" · "}e-mail {settings.channels.email ? "available" : "not configured"}
        {" · "}webhooks {settings.channels.webhook ? "available" : "not configured"}
        {settings.channels.mode === "test" ? " · test mode: e-mail and webhooks are recorded, never sent" : ""}
      </p>
      {errorNotice(personal.error)}
      {save.error && !(save.error instanceof ApiError && save.error.code === "CONFIRMATION_REQUIRED") ? errorNotice(save.error) : null}
      {saved ? <Notice tone="success">{saved}</Notice> : null}
      <div className="table-wrap">
        <table className="table">
          <caption className="visually-hidden">Notification types for {settings.organization.login}</caption>
          <thead>
            <tr>
              <th scope="col">Notification</th>
              <th scope="col">My inbox</th>
              <th scope="col">Organization in-app</th>
              <th scope="col">E-mail</th>
              <th scope="col">Webhook</th>
            </tr>
          </thead>
          <tbody>
            {settings.types.map((t) => {
              const channels = draft[t.type] ?? t.organization;
              return (
                <tr key={t.type}>
                  <td data-label="Notification" className="table__primary">
                    <span className="stack">
                      <span className="strong">{t.label}</span>
                      <span className="muted small">{t.description}</span>
                    </span>
                  </td>
                  <td data-label="My inbox">
                    {!t.receives_in_app ? (
                      <span className="muted small">Not for your role</span>
                    ) : t.mandatory_in_app ? (
                      <span className="muted small"><Lock size={12} aria-hidden="true" /> Always</span>
                    ) : (
                      <label className="switch">
                        <input
                          type="checkbox"
                          checked={t.personal_in_app}
                          disabled={personal.isPending || !t.organization.in_app}
                          onChange={(e) => personal.mutate({ type: t.type, value: e.target.checked })}
                        />
                        <span className="visually-hidden">Show {t.label} in my inbox</span>
                      </label>
                    )}
                  </td>
                  {(Object.keys(CHANNEL_LABEL) as (keyof Channels)[]).map((channel) => (
                    <td key={channel} data-label={CHANNEL_LABEL[channel]}>
                      {settings.can_manage && !(channel === "in_app" && t.mandatory_in_app) ? (
                        <label className="switch">
                          <input type="checkbox" checked={channels[channel]} onChange={(e) => setChannel(t.type, channel, e.target.checked)} />
                          <span className="visually-hidden">{CHANNEL_LABEL[channel]} for {t.label}</span>
                        </label>
                      ) : (
                        <span className="small">{channels[channel] ? "On" : "Off"}{channel === "in_app" && t.mandatory_in_app ? " (mandatory)" : ""}</span>
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {settings.can_manage ? (
        <>
          <div className="field">
            <label htmlFor={`recipients-${organization}`}>E-mail recipients (one per line)</label>
            <textarea id={`recipients-${organization}`} rows={3} value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="security@example.com" />
          </div>
          <div className="panel__footer">
            <button type="button" className="button button--secondary" disabled={!dirty} onClick={() => { setDraft(original); setRecipients(settings.email_recipients.join("\n")); }}>Discard</button>
            <button
              type="button"
              className="button button--primary"
              disabled={!dirty || save.isPending}
              onClick={() => {
                setSaved(null);
                const off = [...turnedOff, ...(removedRecipients.length ? [`E-mail recipients removed: ${removedRecipients.length}`] : [])];
                if (off.length) setPendingOff(off);
                else save.mutate(false);
              }}
            >
              {save.isPending ? "Saving…" : "Save organization settings"}
            </button>
          </div>
          <h3 className="subheading">Webhooks</h3>
          <Webhooks settings={settings} />
          <h3 className="subheading">Recent deliveries</h3>
          <Deliveries organization={organization} />
        </>
      ) : (
        <p className="muted small"><Lock size={12} aria-hidden="true" /> Organization delivery settings are managed by admins and owners.</p>
      )}
      <ConfirmDialog
        open={pendingOff !== null}
        title="Turn off notification deliveries?"
        confirmLabel="Turn off deliveries"
        onCancel={() => setPendingOff(null)}
        onConfirm={() => save.mutate(true)}
        busy={save.isPending}
      >
        <p>People and systems relying on these notifications will stop receiving them:</p>
        <ul>{(pendingOff ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
        <p className="muted small">Security decisions are not affected: CommitGuard checks still block commits.</p>
      </ConfirmDialog>
    </div>
  );
}
