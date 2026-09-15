import { readFileSync } from "node:fs";
import { test, type Page } from "@playwright/test";

/**
 * README header and evidence images, rendered from docs/evidence/*.json, which
 * scripts/readme_evidence.py writes from real test, check and CLI runs.
 */

const OUT = "../docs/images";
const EVIDENCE = "../docs/evidence";

type Suite = { name: string; label: string; ok: boolean; passed: number; failed: number; skipped: number; seconds: number; summary: string };
type Check = { name: string; label: string; ok: boolean; seconds: number; summary: string };
type Tests = { generated_at: string; commit: string; python: string; suites: Suite[]; checks: Check[] };
type Step = { command: string; exit: number; output: string };
type Session = { id: string; title: string; cwd: string; steps: Step[] };
type Cli = { version: string; sessions: Session[] };

const font = (path: string) => readFileSync(`node_modules/${path}`).toString("base64");
const FONTS = `
@font-face { font-family: Display; src: url(data:font/woff2;base64,${font("@fontsource-variable/bricolage-grotesque/files/bricolage-grotesque-latin-standard-normal.woff2")}) format("woff2"); font-weight: 200 800; font-stretch: 75% 100%; }
@font-face { font-family: Body; src: url(data:font/woff2;base64,${font("@fontsource-variable/public-sans/files/public-sans-latin-wght-normal.woff2")}) format("woff2"); font-weight: 100 900; }
@font-face { font-family: Mono; src: url(data:font/woff2;base64,${font("@fontsource/commit-mono/files/commit-mono-latin-400-normal.woff2")}) format("woff2"); font-weight: 400; }
@font-face { font-family: Mono; src: url(data:font/woff2;base64,${font("@fontsource/commit-mono/files/commit-mono-latin-700-normal.woff2")}) format("woff2"); font-weight: 700; }
`;

const THEMES = {
  light: {
    paper: "#f2f4f3", surface: "#ffffff", sunken: "#e8ecea", ink: "#101816", soft: "#2b3632", graphite: "#55605c",
    rule: "#d5dcd9", strong: "#b4bfba", pass: "#147a4b", passTint: "#e2f1e8", blocked: "#b42329", blockedTint: "#fae6e5",
    warning: "#855700", warningTint: "#f8eed6", running: "#1a5bb3", runningTint: "#e3ecf9",
  },
  dark: {
    paper: "#0c1110", surface: "#121917", sunken: "#090d0c", ink: "#e4ebe8", soft: "#c4cec9", graphite: "#97a39e",
    rule: "#242e2a", strong: "#35423d", pass: "#5ccb94", passTint: "rgb(92 203 148 / 0.12)", blocked: "#ff7b73",
    blockedTint: "rgb(255 123 115 / 0.12)", warning: "#e5b24d", warningTint: "rgb(229 178 77 / 0.12)", running: "#7cb0ff",
    runningTint: "rgb(124 176 255 / 0.12)",
  },
};
type Theme = (typeof THEMES)["light"];

const vars = (t: Theme) =>
  `:root{${Object.entries(t).map(([k, v]) => `--${k}:${v}`).join(";")}}`;

const escape = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const LOGO = `<svg viewBox="0 0 32 32" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="2.6"><path d="M16 2.5v8M16 21.5v8" stroke-linecap="round"/><circle cx="16" cy="16" r="5"/><path d="M8.5 7.5H4.5v17h4M23.5 7.5h4v17h-4"/></g></svg>`;
const CHECK = `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.2 8.2l1.9 1.9 3.8-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const CROSS = `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
const BLOCK = `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3.4 12.6l9.2-9.2" stroke="currentColor" stroke-width="1.5"/></svg>`;
const WARN = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8l6.6 11.7H1.4z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6.2v3.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="8" cy="11.4" r=".9" fill="currentColor"/></svg>`;

async function render(page: Page, html: string, path: string, width: number) {
  await page.setViewportSize({ width, height: 200 });
  await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>${FONTS}</style></head><body>${html}</body></html>`);
  await page.evaluate(() => document.fonts.ready);
  await page.locator("#card").screenshot({ path, omitBackground: true });
}

function banner(t: Theme) {
  const commits = [
    { sha: "d14c498", message: "chore: initial commit", state: "pass" },
    { sha: "8a2e7f1", message: "feat: add login form", state: "pass" },
    { sha: "6fe6514", message: "Co-authored-by: Claude &lt;noreply@anthropic.com&gt;", state: "blocked" },
    { sha: "c0b19d3", message: "author dependabot[bot]", state: "warning" },
    { sha: "42c0fdb", message: "fix: remove AI attribution", state: "pass" },
  ];
  const label = { pass: `${CHECK}PASS`, blocked: `${BLOCK}BLOCK`, warning: `${WARN}WARN` } as const;
  const rows = commits
    .map(
      (c, i) => `<div class="row ${c.state}" style="--i:${i}">
        <span class="node"></span>
        <code class="sha">${c.sha}</code>
        <span class="msg">${c.message}</span>
        <span class="chip">${label[c.state as keyof typeof label]}</span>
      </div>`,
    )
    .join("");
  return `<style>${vars(t)}
    *{box-sizing:border-box;margin:0}
    body{background:transparent}
    #card{width:1200px;height:380px;position:relative;overflow:hidden;border-radius:18px;background:var(--paper);border:1px solid var(--rule);display:grid;grid-template-columns:1fr 560px;color:var(--ink);font-family:Body}
    #card::before{content:"";position:absolute;inset:0;background-image:linear-gradient(var(--rule) 1px,transparent 1px),linear-gradient(90deg,var(--rule) 1px,transparent 1px);background-size:32px 32px;opacity:.45;mask-image:linear-gradient(90deg,transparent 30%,#000 75%)}
    .left{position:relative;padding:56px 0 48px 64px;display:flex;flex-direction:column}
    .brand{display:flex;align-items:center;gap:16px}
    .brand svg{width:58px;height:58px;color:var(--ink)}
    h1{font-family:Display;font-weight:700;font-size:62px;letter-spacing:-.022em;font-stretch:96%;line-height:1}
    h1 span{color:var(--graphite)}
    .tag{margin-top:26px;font-size:25px;line-height:1.3;color:var(--soft);max-width:520px;letter-spacing:-.01em}
    .flow{margin-top:auto;font-family:Mono;font-size:14.5px;color:var(--graphite);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
    .flow b{font-weight:400;color:var(--ink);border:1px solid var(--strong);background:var(--surface);border-radius:6px;padding:5px 9px}
    .right{position:relative;padding:44px 48px 0 8px;display:flex;flex-direction:column;justify-content:center}
    .graph{position:relative;background:var(--surface);border:1px solid var(--rule);border-radius:12px;padding:12px 18px;box-shadow:0 18px 40px -26px rgb(0 0 0 / .35);margin-bottom:40px}
    .graph::before{content:"";position:absolute;left:27px;top:28px;bottom:28px;width:2px;background:var(--strong)}
    .row{position:relative;display:grid;grid-template-columns:20px 72px 1fr auto;align-items:center;gap:10px;height:52px;border-bottom:1px dashed var(--rule)}
    .row:last-child{border-bottom:0}
    .node{width:14px;height:14px;border-radius:50%;background:var(--surface);border:2.5px solid var(--graphite);justify-self:center;position:relative;z-index:1}
    .sha{font-family:Mono;font-size:13.5px;color:var(--graphite)}
    .msg{font-family:Mono;font-size:13.5px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .chip{display:inline-flex;align-items:center;gap:5px;font-family:Mono;font-weight:700;font-size:11.5px;letter-spacing:.06em;padding:3px 7px;border-radius:4px;border:1px solid}
    .chip svg{width:13px;height:13px}
    .pass .chip{color:var(--pass);background:var(--passTint);border-color:color-mix(in srgb,var(--pass) 35%,transparent)}
    .warning .chip{color:var(--warning);background:var(--warningTint);border-color:color-mix(in srgb,var(--warning) 35%,transparent)}
    .blocked .chip{color:var(--blocked);background:var(--blockedTint);border-color:color-mix(in srgb,var(--blocked) 35%,transparent)}
    .blocked{background:linear-gradient(90deg,var(--blockedTint),transparent 80%);margin:0 -18px;padding:0 18px 0 15px;border-left:3px solid var(--blocked)}
    .blocked .node{border-color:var(--blocked);background:var(--blocked);outline:4px solid var(--blockedTint)}
    .blocked .msg{color:var(--blocked)}
  </style>
  <div id="card">
    <div class="left">
      <div class="brand">${LOGO}<h1>Commit<span>Guard</span></h1></div>
      <p class="tag">Git commit provenance and contribution policy enforcement.</p>
      <div class="flow"><b>git commit</b>→<b>detect</b>→<b>policy</b>→<b>allow · warn · block</b></div>
    </div>
    <div class="right"><div class="graph">${rows}</div></div>
  </div>`;
}

function terminal(session: Session) {
  const body = session.steps
    .map((step) => {
      const lines = escape(step.output)
        .replace(/^(✗.*)$/gm, '<span class="bad">$1</span>')
        .replace(/^(Result: BLOCK|result=BLOCK.*)$/gm, '<span class="bad">$1</span>')
        .replace(/^(BLOCK)\t/gm, '<span class="bad">$1</span>\t')
        .replace(/^(CommitGuard)$/gm, '<span class="head">$1</span>');
      return `<div class="step"><div class="cmd"><span class="cwd">${escape(session.cwd)}</span> <span class="prompt">$</span> ${escape(step.command)}</div><pre>${lines}</pre><div class="exit${step.exit === 0 ? " zero" : ""}">exit ${step.exit}</div></div>`;
    })
    .join("");
  return `<style>
    *{box-sizing:border-box;margin:0}
    #card{width:980px;background:#070a09;border:1px solid #1d2623;border-radius:14px;overflow:hidden;color:#dfe7e3;font-family:Mono;font-size:14px;line-height:1.55}
    .bar{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid #1d2623;background:#0c1110;color:#7f8c87;font-size:12.5px}
    .bar svg{width:18px;height:18px;color:#dfe7e3}
    .bar .title{color:#c4cec9;font-family:Body;font-weight:600;font-size:13.5px}
    .bar .ver{margin-left:auto}
    .step{padding:16px 22px 14px;border-bottom:1px solid #1d2623}
    .step:last-child{border-bottom:0}
    .cmd{color:#dfe7e3;white-space:pre-wrap;word-break:break-word}
    .cwd{color:#7cb0ff}.prompt{color:#7f8c87}
    pre{margin-top:10px;font:inherit;white-space:pre-wrap;tab-size:10;color:#c4cec9}
    .bad{color:#ff8a82;font-weight:700}.head{color:#dfe7e3;font-weight:700}
    .exit{margin-top:10px;display:inline-block;font-size:11.5px;font-weight:700;letter-spacing:.06em;color:#ff8a82;border:1px solid rgb(255 138 130 / .35);background:rgb(255 138 130 / .1);border-radius:4px;padding:2px 7px}
    .exit.zero{color:#5ccb94;border-color:rgb(92 203 148 / .35);background:rgb(92 203 148 / .1)}
  </style>
  <div id="card"><div class="bar">${LOGO}<span class="title">${escape(session.title)}</span></div>${body}</div>`;
}

function results(t: Theme, data: Tests) {
  const total = data.suites.reduce((sum, s) => sum + s.passed, 0);
  const failed = data.suites.reduce((sum, s) => sum + s.failed, 0);
  const failedChecks = data.checks.filter((c) => !c.ok).length;
  const allOk = failed === 0 && failedChecks === 0 && data.suites.every((s) => s.ok);
  const status = allOk
    ? `${total.toLocaleString("en-US")} TESTS PASSED`
    : failed > 0
      ? `${failed} TEST${failed === 1 ? "" : "S"} FAILED`
      : `${failedChecks || "SUITE"} CHECK${failedChecks === 1 ? "" : "S"} FAILED`;
  const date = new Date(data.generated_at).toISOString().slice(0, 10);
  const suites = data.suites
    .map(
      (s) => `<div class="suite ${s.ok ? "ok" : "fail"}">
        <div class="name">${s.ok ? CHECK : CROSS}<code>${escape(s.name)}</code></div>
        <div class="num">${s.passed.toLocaleString("en-US")}</div>
        <div class="label">${escape(s.label)}</div>
        <div class="meta">${s.failed} failed · ${s.skipped} skipped · ${Math.round(s.seconds)} s</div>
      </div>`,
    )
    .join("");
  const checks = data.checks
    .map((c) => `<div class="check ${c.ok ? "ok" : "fail"}">${c.ok ? CHECK : CROSS}<span>${escape(c.label)}</span></div>`)
    .join("");
  return `<style>${vars(t)}
    *{box-sizing:border-box;margin:0}
    #card{width:1100px;background:var(--surface);border:1px solid var(--rule);border-radius:16px;color:var(--ink);font-family:Body;overflow:hidden}
    header{display:flex;align-items:center;gap:14px;padding:22px 30px;border-bottom:1px solid var(--rule)}
    header svg{width:26px;height:26px}
    h2{font-family:Display;font-weight:700;font-size:24px;letter-spacing:-.01em;font-stretch:96%}
    .status{margin-left:auto;display:inline-flex;align-items:center;gap:7px;font-family:Mono;font-weight:700;font-size:13px;letter-spacing:.06em;padding:5px 10px;border-radius:5px;border:1px solid}
    .status svg{width:15px;height:15px}
    .status.ok{color:var(--pass);background:var(--passTint);border-color:color-mix(in srgb,var(--pass) 35%,transparent)}
    .status.fail{color:var(--blocked);background:var(--blockedTint);border-color:color-mix(in srgb,var(--blocked) 35%,transparent)}
    .grid{display:grid;grid-template-columns:repeat(3,1fr)}
    .suite{padding:24px 30px;border-right:1px solid var(--rule)}
    .suite:last-child{border-right:0}
    .name{display:flex;align-items:center;gap:8px;font-family:Mono;font-size:14px;color:var(--graphite)}
    .name svg{width:17px;height:17px}
    .ok .name svg,.check.ok svg{color:var(--pass)}.fail .name svg,.check.fail svg{color:var(--blocked)}
    .num{margin-top:12px;font-family:Display;font-weight:700;font-size:54px;letter-spacing:-.015em;line-height:1;font-stretch:100%;font-variant-numeric:tabular-nums}
    .label{margin-top:6px;font-size:15px;color:var(--soft)}
    .meta{margin-top:10px;font-family:Mono;font-size:12.5px;color:var(--graphite)}
    .checks{display:flex;gap:10px;flex-wrap:wrap;padding:18px 30px;border-top:1px solid var(--rule);background:var(--sunken)}
    .check{display:inline-flex;align-items:center;gap:7px;font-size:14px;padding:6px 11px;border:1px solid var(--rule);border-radius:6px;background:var(--surface)}
    .check svg{width:16px;height:16px}
    footer{display:flex;gap:18px;padding:14px 30px;border-top:1px solid var(--rule);font-family:Mono;font-size:12.5px;color:var(--graphite)}
    footer span:last-child{margin-left:auto}
  </style>
  <div id="card">
    <header>${LOGO}<h2>Test evidence</h2><span class="status ${allOk ? "ok" : "fail"}">${allOk ? CHECK : CROSS}${status}</span></header>
    <div class="grid">${suites}</div>
    <div class="checks">${checks}</div>
    <footer><span>commit ${escape(data.commit)}</span><span>Python ${escape(data.python)}</span><span>generated ${date} by scripts/readme_evidence.py</span></footer>
  </div>`;
}

test("README header and evidence images", async ({ browser }) => {
  const context = await browser.newContext({ deviceScaleFactor: 2 });
  const page = await context.newPage();

  await render(page, banner(THEMES.light), `${OUT}/banner-light.png`, 1240);
  await render(page, banner(THEMES.dark), `${OUT}/banner-dark.png`, 1240);

  const cli = JSON.parse(readFileSync(`${EVIDENCE}/cli.json`, "utf8")) as Cli;
  for (const session of cli.sessions) {
    await render(page, terminal(session), `${OUT}/cli-${session.id}.png`, 1020);
  }

  const tests = JSON.parse(readFileSync(`${EVIDENCE}/tests.json`, "utf8")) as Tests;
  await render(page, results(THEMES.light, tests), `${OUT}/tests-light.png`, 1140);
  await render(page, results(THEMES.dark, tests), `${OUT}/tests-dark.png`, 1140);

  await context.close();
});
