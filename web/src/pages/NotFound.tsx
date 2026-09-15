import { ArrowLeft, LayoutDashboard } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router";

import { LogoMark } from "../components/Logo";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { routes } from "../lib/routes";

/**
 * Not found - set as the error Git itself prints for a revision it cannot
 * resolve. Deliberately identical for "never existed" and "not yours".
 */
export function NotFoundContent({ resource = "page" }: { resource?: string }) {
  const navigate = useNavigate();
  const location = useLocation();
  const requested = location.pathname.slice(0, 80);
  return (
    <section className="not-found" aria-labelledby="not-found-title">
      <p className="not-found__code" aria-hidden="true">
        4<span className="not-found__node"><LogoMark size={120} /></span>4
      </p>
      <h1 id="not-found-title" className="not-found__title">
        {resource === "page" ? "404 Not Found" : `This ${resource} was not found`}
      </h1>
      <pre className="not-found__terminal">
        <span className="muted">$ git show {requested}</span>
        {"\n"}
        <span className="not-found__fatal">fatal:</span> ambiguous argument &apos;{requested}&apos;: unknown revision or path not in the working tree.
      </pre>
      <p className="not-found__body">
        It does not exist, or it is not available to your account. CommitGuard gives the same answer in both cases, so it never reveals what belongs to another organization.
      </p>
      <div className="not-found__actions">
        <button type="button" className="button button--secondary" onClick={() => navigate(-1)}>
          <ArrowLeft size={14} aria-hidden="true" /> Go back
        </button>
        <Link className="button button--primary" to={routes.overview}>
          <LayoutDashboard size={14} aria-hidden="true" /> Go to overview
        </Link>
      </div>
    </section>
  );
}

export default function NotFound() {
  useDocumentTitle("Not found");
  return (
    <div className="standalone">
      <Link to="/" className="standalone__brand" aria-label="CommitGuard home">
        <LogoMark size={26} />
      </Link>
      <NotFoundContent />
    </div>
  );
}
