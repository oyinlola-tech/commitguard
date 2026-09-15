import { ArrowLeft, LayoutDashboard } from "lucide-react";
import { Link, useNavigate } from "react-router";

import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { routes } from "../lib/routes";

/** Deliberately identical for "never existed" and "exists but not yours". */
export function NotFoundContent({ resource = "page" }: { resource?: string }) {
  const navigate = useNavigate();
  return (
    <section className="not-found" aria-labelledby="not-found-title">
      <p className="not-found__code" aria-hidden="true">
        404
      </p>
      <h1 id="not-found-title" className="not-found__title">
        {resource === "page" ? "404 Not Found" : `This ${resource} was not found`}
      </h1>
      <p className="not-found__body">It does not exist, or it is not available to your account.</p>
      <p className="not-found__hint">
        <code>ref not found</code>
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
      <NotFoundContent />
    </div>
  );
}
