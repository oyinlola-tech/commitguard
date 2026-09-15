import type { AuditEvent } from "../api/types";
import { POLICY_ACTION } from "../lib/labels";
import { routes } from "../lib/routes";
import { Badge } from "./Badge";
import { Time } from "./Primitives";
import { Link } from "react-router";

export function actorLabel(event: AuditEvent): string {
  if (event.actor.type === "user") return event.actor.login ?? `user ${event.actor.id ?? ""}`;
  if (event.actor.type === "github") return "GitHub";
  return event.actor.login ?? "CommitGuard";
}

export function AuditTable({ events, caption }: { events: AuditEvent[]; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Timestamp</th>
            <th scope="col">Actor</th>
            <th scope="col">Action</th>
            <th scope="col">Resource</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id}>
              <td data-label="Timestamp"><Time value={event.occurred_at} absolute /></td>
              <td data-label="Actor">{actorLabel(event)}</td>
              <td data-label="Action" className="table__primary">
                <span className="stack">
                  <span>{event.summary}</span>
                  <code className="muted">{event.type}</code>
                </span>
              </td>
              <td data-label="Resource">
                {event.repository ? (
                  <Link to={routes.repository(event.repository.id)}>{event.repository.full_name}</Link>
                ) : event.scan ? (
                  <Link to={routes.scan(event.scan)}>Scan</Link>
                ) : (
                  <span className="muted">Organization</span>
                )}
              </td>
              <td data-label="Result">{event.action ? <Badge map={POLICY_ACTION} value={event.action} compact /> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
