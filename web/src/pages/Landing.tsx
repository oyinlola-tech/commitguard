import { ArrowRight, LogIn } from "lucide-react";
import { Link } from "react-router";

import { signInUrl } from "../api/auth";
import { useOptionalSession } from "../auth/session";
import { PublicLayout } from "../components/layout/PublicLayout";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { routes } from "../lib/routes";

/** The hero's commit, printed the way `git cat-file -p` prints it. */
const COMMIT_LINES: { key: string; text: string; trailer?: boolean; blank?: boolean }[] = [
  { key: "tree", text: "tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904" },
  { key: "parent", text: "parent 3a91f02e7d5c1b86a4e0f9d2c7b3a1e8d6f40c21" },
  { key: "author", text: "author Dana Reyes <dana@northwind.dev> 1757412000 +0000" },
  { key: "committer", text: "committer Dana Reyes <dana@northwind.dev> 1757412000 +0000" },
  { key: "gap-1", text: "", blank: true },
  { key: "subject", text: "feat(payments): verify refund webhook signatures" },
  { key: "gap-2", text: "", blank: true },
  { key: "trailer", text: "Co-authored-by: Claude <noreply@anthropic.com>", trailer: true },
];

const EVIDENCE = [
  {
    rule: "ai_coauthor",
    title: "Co-author trailers",
    body: "A Co-authored-by line naming an AI agent's identity or address.",
    sample: "Co-authored-by: Claude <noreply@anthropic.com>",
  },
  {
    rule: "ai_identity",
    title: "Author and committer",
    body: "An AI agent recorded as the commit's author or committer.",
    sample: "author Cursor Agent <cursoragent@cursor.com>",
  },
  {
    rule: "ai_trailer",
    title: "Attribution footers",
    body: "Generated-by and Assisted-by trailers, and the footers coding tools add to messages.",
    sample: "Generated with Claude Code",
  },
];

const LAYERS = [
  { place: "On the laptop", name: "Git hooks", body: "pre-commit, commit-msg and pre-push stop an attributed commit before it leaves the machine. Fast feedback, but a developer can skip hooks." },
  { place: "On every pull request", name: "GitHub Actions", body: "A workflow evaluates every commit the pull request would merge, using the policy from the base branch so a PR cannot approve itself." },
  { place: "Across the organization", name: "GitHub App", body: "One installation scans every covered repository and posts the commitguard-app check. Nothing to add to each repository's workflows." },
  { place: "For the people responsible", name: "Dashboard", body: "Shows what was scanned, what is blocked right now, which policy decided it, and what changed since the last scan." },
];

const USE_CASES = [
  {
    who: "Security and compliance teams",
    need: "Your policy says AI agents may assist but must not be credited as authors of production code, and auditors want proof it holds.",
    how: "Set an organization floor of BLOCK for ai_coauthor and ai_identity. Repositories can tighten it, never lower it.",
    see: "Every blocked pull request with its evidence, and a policy history showing who changed what, when and why.",
  },
  {
    who: "Platform and developer-experience teams",
    need: "Hundreds of repositories, many teams, and no appetite for copying a workflow file into each one.",
    how: "Install the GitHub App once for the organization. Require the commitguard-app check in a ruleset.",
    see: "Which repositories are verified to require the check, which are not, and where configuration is broken.",
  },
  {
    who: "Open-source maintainers",
    need: "Contributors use AI tools, and your project asks for clear human authorship on every commit.",
    how: "Keep the defaults: attribution blocks, bot accounts such as Dependabot only warn.",
    see: "A check on the pull request that names the exact trailer and tells the contributor how to fix it.",
  },
  {
    who: "Regulated engineering organizations",
    need: "Provenance of source changes has to be explainable months later, under the policy that applied at the time.",
    how: "Keep scan history for your retention period. Each result stores its policy version and rules version.",
    see: "A reproducible record: commit range, effective policy, rules version and CommitGuard version for each decision.",
  },
];

const BLOCKED_FLOW = [
  { title: "A pull request is opened", body: "GitHub notifies CommitGuard. The App fetches commit metadata only: no checkout, no build, no code from the pull request runs." },
  { title: "The check fails with a reason", body: "commitguard-app reports BLOCKED and names the rule, the commit and the Co-authored-by line that triggered it." },
  { title: "The violation opens in the dashboard", body: "Security managers see the evidence, the policy that decided it and the recommended fix. Acknowledging it does not unblock anything." },
  { title: "The developer corrects the commit", body: "They remove the attribution, rewrite the commit and push. CommitGuard never rewrites history for them." },
  { title: "The new scan passes", body: "The violation resolves because the commit is no longer part of the pull request. Its detection history stays in the audit trail." },
];

const NEVER = [
  "Rewrite, amend or force-push your commits",
  "Check out or execute code from a pull request",
  "Call a repository protected without GitHub confirming a required check",
  "Store GitHub tokens, passwords or full commit messages",
  "Let a pull request change the policy it is evaluated with",
  "Decide in the browser: every verdict comes from the CommitGuard core",
];

function CommitRecord() {
  return (
    <figure className="specimen" aria-labelledby="specimen-caption">
      <div className="specimen__chrome">
        <span className="specimen__prompt">
          <span aria-hidden="true">$ </span>git cat-file -p 8e71c2a
        </span>
        <span className="specimen__check">commitguard-app</span>
      </div>
      <pre className="specimen__body" aria-hidden="true">
        {COMMIT_LINES.map((line) =>
          line.blank ? (
            <span key={line.key} className="specimen__line specimen__line--blank">{" "}</span>
          ) : (
            <span key={line.key} className={line.trailer ? "specimen__line specimen__line--trailer" : "specimen__line"}>
              {line.text}
            </span>
          ),
        )}
        <span className="specimen__scan" />
      </pre>
      <div className="stamp" aria-hidden="true">
        <span className="stamp__verdict">Blocked</span>
        <span className="stamp__rule">ai_coauthor · high</span>
        <span className="stamp__policy">org policy v4 · base 3a91f02</span>
      </div>
      <figcaption id="specimen-caption" className="specimen__caption">
        Illustration: a commit whose Co-authored-by trailer names an AI agent. CommitGuard blocks the pull request and says exactly which line and which rule.
      </figcaption>
    </figure>
  );
}

export default function Landing() {
  useDocumentTitle(null);
  const session = useOptionalSession();
  const primary = session.data ? (
    <Link className="button button--primary button--large" to={routes.overview}>
      Open dashboard <ArrowRight size={16} aria-hidden="true" />
    </Link>
  ) : (
    <a className="button button--primary button--large" href={signInUrl(routes.overview)}>
      <LogIn size={16} aria-hidden="true" /> Sign in with GitHub
    </a>
  );
  return (
    <PublicLayout>
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero__grid">
          <div className="hero__copy">
            <p className="eyebrow">Commit provenance enforcement for GitHub</p>
            <h1 id="hero-title" className="hero__title">
              Every commit makes a claim about who wrote it.
            </h1>
            <p className="hero__lead">
              CommitGuard reads that claim - author, committer, co-author trailers - and enforces your contribution policy before the commit merges. When it blocks one, it tells you exactly why.
            </p>
            <div className="hero__actions">
              {primary}
              <a className="button button--quiet button--large" href="#use-cases">
                See who it's for
              </a>
            </div>
            <dl className="hero__facts">
              <div><dt>GitHub access</dt><dd>Read-only, plus Checks</dd></div>
              <div><dt>Code executed</dt><dd>None</dd></div>
              <div><dt>History rewritten</dt><dd>Never</dd></div>
            </dl>
          </div>
          <CommitRecord />
        </div>
      </section>

      <section id="evidence" className="section" aria-labelledby="evidence-title">
        <div className="section__inner">
          <header className="section__header">
            <p className="eyebrow">What it reads</p>
            <h2 id="evidence-title" className="section__title">Commit metadata, not your source code</h2>
            <p className="section__lead">Detection runs on the parts of a commit that make an authorship claim. File contents are never read or stored.</p>
          </header>
          <div className="evidence-grid">
            {EVIDENCE.map((item) => (
              <article className="evidence-card" key={item.rule}>
                <code className="evidence-card__rule">{item.rule}</code>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
                <pre className="evidence-card__sample">{item.sample}</pre>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="enforcement" className="section section--ruled" aria-labelledby="layers-title">
        <div className="section__inner">
          <header className="section__header">
            <p className="eyebrow">Where it enforces</p>
            <h2 id="layers-title" className="section__title">From the laptop to the merge button</h2>
            <p className="section__lead">The same detection engine and the same policy engine run at every layer, so a commit gets the same answer wherever it is checked.</p>
          </header>
          <ol className="branch">
            {LAYERS.map((layer) => (
              <li className="branch__stop" key={layer.name}>
                <span className="branch__node" aria-hidden="true" />
                <p className="branch__place">{layer.place}</p>
                <h3 className="branch__name">{layer.name}</h3>
                <p>{layer.body}</p>
              </li>
            ))}
          </ol>
          <p className="section__note">
            A failing check blocks a merge only when branch protection or a ruleset requires it. CommitGuard shows whether that is verified; it does not change your GitHub settings.
          </p>
        </div>
      </section>

      <section id="use-cases" className="section" aria-labelledby="use-cases-title">
        <div className="section__inner">
          <header className="section__header">
            <p className="eyebrow">Who it's for</p>
            <h2 id="use-cases-title" className="section__title">Four teams, one record of authorship</h2>
          </header>
          <div className="use-cases">
            {USE_CASES.map((useCase) => (
              <article className="use-case" key={useCase.who}>
                <h3 className="use-case__who">{useCase.who}</h3>
                <dl className="use-case__parts">
                  <div><dt>The situation</dt><dd>{useCase.need}</dd></div>
                  <div><dt>What you set up</dt><dd>{useCase.how}</dd></div>
                  <div><dt>What you see</dt><dd>{useCase.see}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="blocked" className="section section--ink" aria-labelledby="blocked-title">
        <div className="section__inner section__inner--split">
          <header className="section__header">
            <p className="eyebrow">When a commit is blocked</p>
            <h2 id="blocked-title" className="section__title">What happens, in order</h2>
            <p className="section__lead">Blocking is only useful if the person blocked can fix it in minutes. Every step gives them the reason and the next action.</p>
          </header>
          <ol className="flow">
            {BLOCKED_FLOW.map((step) => (
              <li key={step.title} className="flow__step">
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section id="principles" className="section" aria-labelledby="never-title">
        <div className="section__inner section__inner--split">
          <header className="section__header">
            <p className="eyebrow">Security model</p>
            <h2 id="never-title" className="section__title">What CommitGuard will never do</h2>
            <p className="section__lead">A tool that checks your engineers has to be easy to trust. These limits are enforced in code and covered by tests.</p>
          </header>
          <ul className="never">
            {NEVER.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </section>

      <section className="cta" aria-labelledby="cta-title">
        <div className="cta__inner">
          <h2 id="cta-title">See what your repositories are merging.</h2>
          <p>Sign in with the GitHub account that can see your organization's CommitGuard installation.</p>
          {primary}
        </div>
      </section>
    </PublicLayout>
  );
}
