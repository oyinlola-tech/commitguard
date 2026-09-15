import { ArrowRight, Ban, BookOpen, CircleCheck, FileCode, GitPullRequest, LayoutDashboard, LogIn, Scale, ScrollText, ShieldCheck, SquareTerminal, Webhook } from "lucide-react";
import { Link } from "react-router";

import { signInUrl } from "../api/auth";
import { useOptionalSession } from "../auth/session";
import { PublicLayout } from "../components/layout/PublicLayout";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { routes } from "../lib/routes";

const PIPELINE = [
  { icon: SquareTerminal, title: "Git hooks", body: "Stop attributed commits on the developer's machine before they are pushed." },
  { icon: FileCode, title: "GitHub Actions", body: "Evaluate every pull request and push with the trusted base-branch policy." },
  { icon: Webhook, title: "GitHub App", body: "Centralised scanning and Check Runs for every installed repository." },
  { icon: LayoutDashboard, title: "Dashboard", body: "Explain what was detected, what is blocked, and why." },
];

const PRINCIPLES = [
  { icon: Scale, title: "The core decides", body: "One detection engine and one policy engine produce every result - CLI, hooks, Actions, App and dashboard show the same decision." },
  { icon: GitPullRequest, title: "GitHub enforces", body: "A failing check blocks a merge only when branch protection requires it. The dashboard shows whether that is verified, never assumes it." },
  { icon: ScrollText, title: "Everything is audited", body: "Policy changes, acknowledgements and scans are recorded with the actor and time. History is never rewritten - neither yours nor ours." },
];

export default function Landing() {
  useDocumentTitle(null);
  const session = useOptionalSession();
  return (
    <PublicLayout>
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero__grid">
          <div className="hero__copy">
            <p className="hero__eyebrow">
              <ShieldCheck size={14} aria-hidden="true" /> Commit provenance control plane
            </p>
            <h1 id="hero-title" className="hero__title">
              Know what reaches your branches <span className="hero__accent">- and why it was stopped.</span>
            </h1>
            <p className="hero__lead">
              CommitGuard detects AI agent attribution in commit metadata, enforces your contribution policy on GitHub, and keeps an auditable record of every decision.
            </p>
            <div className="hero__actions">
              {session.data ? (
                <Link className="button button--primary button--large" to={routes.overview}>
                  Open dashboard <ArrowRight size={16} aria-hidden="true" />
                </Link>
              ) : (
                <a className="button button--primary button--large" href={signInUrl(routes.overview)}>
                  <LogIn size={16} aria-hidden="true" /> Sign in with GitHub
                </a>
              )}
              <a className="button button--ghost button--large" href="#how-it-works">
                <BookOpen size={16} aria-hidden="true" /> How it works
              </a>
            </div>
            <p className="hero__fineprint">Read-only GitHub access for scanning. No code is checked out or executed.</p>
          </div>
          <figure className="hero__visual" aria-label="Example of a CommitGuard check result">
            <div className="check-card">
              <div className="check-card__header">
                <span className="check-card__label">Example check · commitguard-app</span>
                <span className="badge badge--danger">
                  <Ban size={14} aria-hidden="true" /> BLOCKED
                </span>
              </div>
              <dl className="check-card__meta">
                <div><dt>Pull request</dt><dd>#128 · feature/payments</dd></div>
                <div><dt>Commits scanned</dt><dd>3</dd></div>
                <div><dt>Policy</dt><dd>Organization policy v4</dd></div>
              </dl>
              <div className="check-card__finding">
                <p className="check-card__rule">
                  <code>ai_coauthor</code>
                  <span className="badge badge--danger badge--compact">HIGH</span>
                </p>
                <p className="check-card__evidence">
                  <span className="muted">Co-authored-by trailer</span>
                  <code>Example Agent &lt;agent@example.com&gt;</code>
                </p>
                <p className="check-card__remediation">Remove the attribution and push the corrected commit. CommitGuard does not rewrite history.</p>
              </div>
              <div className="check-card__footer">
                <CircleCheck size={14} aria-hidden="true" /> Evaluated with the base branch's trusted policy
              </div>
            </div>
            <figcaption className="visually-hidden">Illustrative example; not data from your organization.</figcaption>
          </figure>
        </div>
      </section>

      <section id="how-it-works" className="landing-section" aria-labelledby="how-title">
        <div className="landing-section__inner">
          <p className="section-eyebrow">How it works</p>
          <h2 id="how-title" className="section-title">One decision, enforced at every layer</h2>
          <ol className="pipeline" id="enforcement">
            {PIPELINE.map((step, index) => {
              const Icon = step.icon;
              return (
                <li className="pipeline__step" key={step.title}>
                  <span className="pipeline__index" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <Icon size={20} aria-hidden="true" className="pipeline__icon" />
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </li>
              );
            })}
          </ol>
        </div>
      </section>

      <section id="principles" className="landing-section landing-section--alt" aria-labelledby="principles-title">
        <div className="landing-section__inner">
          <p className="section-eyebrow">Security model</p>
          <h2 id="principles-title" className="section-title">Built to be trusted by the people it checks</h2>
          <div className="principles">
            {PRINCIPLES.map((item) => {
              const Icon = item.icon;
              return (
                <article className="principle" key={item.title}>
                  <Icon size={20} aria-hidden="true" />
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </article>
              );
            })}
          </div>
        </div>
      </section>
    </PublicLayout>
  );
}
