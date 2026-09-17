import { Lock } from "lucide-react";

import { useSession } from "../auth/session";
import { AuditLogView } from "../components/AuditLogView";
import { PageHeader } from "../components/Primitives";
import { useDocumentTitle } from "../hooks/useDocumentTitle";

export default function Audit() {
  useDocumentTitle("Audit log");
  const { organization } = useSession();
  return (
    <>
      <PageHeader
        title="Audit log"
        description={<><Lock size={12} aria-hidden="true" /> Security-relevant actions, recorded when they happen. Events cannot be edited or deleted here; retention removes them after the configured period.</>}
      />
      <AuditLogView organization={organization} />
    </>
  );
}
