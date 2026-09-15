import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { DataTable } from "./DataTable";
import { ErrorBoundary } from "./ErrorBoundary";
import { EvidenceList } from "./Evidence";
import { ExternalLink } from "./Primitives";
import { ErrorState } from "./States";
import { ApiError } from "../api/client";
import { PROTECTION, SCAN_RESULT, SEVERITY, VIOLATION_STATUS } from "../lib/labels";
import { routes } from "../lib/routes";
import { renderWithProviders } from "../test/render";

describe("status vocabulary", () => {
  it.each([
    ["pass", "PASS"],
    ["warning", "WARNING"],
    ["blocked", "BLOCKED"],
    ["error", "ERROR"],
    ["running", "RUNNING"],
    ["queued", "QUEUED"],
    ["cancelled", "CANCELLED"],
  ])("renders scan result %s as the text %s", (value, label) => {
    render(<Badge map={SCAN_RESULT} value={value} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("never shows colour alone and never softens a block", () => {
    const { container } = render(
      <>
        <Badge map={SCAN_RESULT} value="blocked" />
        <Badge map={VIOLATION_STATUS} value="open" />
        <Badge map={PROTECTION} value="unknown" />
        <Badge map={SEVERITY} value="critical" />
      </>,
    );
    expect(container.textContent).toBe("BLOCKEDOPENUNKNOWNCRITICAL");
    expect(screen.getByText("BLOCKED").closest(".badge")).toHaveClass("badge--danger");
    expect(screen.queryByText("WARNING")).toBeNull();
  });

  it("shows UNKNOWN for a value outside the vocabulary", () => {
    render(<Badge map={SCAN_RESULT} value="definitely_new" />);
    expect(screen.getByText("UNKNOWN")).toBeInTheDocument();
  });
});

describe("untrusted content", () => {
  it("renders commit metadata as text, never HTML", () => {
    const payload = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>';
    const { container } = render(
      <EvidenceList evidence={[{ source: "coauthor_trailer", source_label: "Co-authored-by trailer", value: payload, line_number: 3, matched: [], notes: [payload] }]} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getAllByText(payload)).toHaveLength(2);
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
  });

  it("only links to GitHub over HTTPS", () => {
    render(
      <>
        <ExternalLink href="https://github.com/octo-org/project">safe</ExternalLink>
        <ExternalLink href="javascript:alert(1)">script</ExternalLink>
        <ExternalLink href="https://github.com.evil.example/x">lookalike</ExternalLink>
      </>,
    );
    expect(screen.getByRole("link", { name: /safe/ })).toHaveAttribute("rel", "noreferrer noopener");
    expect(screen.queryByRole("link", { name: /script/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /lookalike/ })).toBeNull();
  });

  it("percent-encodes route parameters", () => {
    expect(routes.rule("../../admin?x=1")).toBe("/rules/..%2F..%2Fadmin%3Fx%3D1");
  });

  it("never uses dangerouslySetInnerHTML or browser storage for security data", () => {
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const path = join(dir, entry);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.tsx?$/.test(entry) && !entry.includes(".test.")) files.push(path);
      }
    };
    walk(join(__dirname, ".."));
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      const code = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
      expect(code, file).not.toContain("dangerouslySetInnerHTML");
      expect(code, file).not.toMatch(/sessionStorage/);
      if (!file.endsWith("preferences.ts")) expect(code, file).not.toMatch(/localStorage/);
    }
  });
});

describe("DataTable", () => {
  it("labels every cell for the stacked mobile layout and links the primary cell", () => {
    renderWithProviders(
      <DataTable
        caption="Scans"
        rows={[{ id: "1", name: "octo-org/api" }]}
        rowKey={(r) => r.id}
        rowHref={() => "/scans/1"}
        columns={[
          { key: "name", header: "Repository", primary: true, cell: (r) => r.name },
          { key: "result", header: "Result", cell: () => "BLOCKED" },
        ]}
      />,
    );
    const table = screen.getByRole("table", { name: "Scans" });
    const cells = within(table).getAllByRole("cell");
    expect(cells.map((c) => c.getAttribute("data-label"))).toEqual(["Repository", "Result"]);
    expect(within(table).getByRole("link", { name: "octo-org/api" })).toHaveAttribute("href", "/scans/1");
  });
});

describe("ConfirmDialog", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    const [ok, setOk] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>Open</button>
        <ConfirmDialog open={open} title="Weaken?" confirmLabel="Weaken" onCancel={() => setOpen(false)} onConfirm={() => setOpen(false)} confirmDisabled={!ok}>
          <label><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I understand</label>
        </ConfirmDialog>
      </>
    );
  }

  it("is modal, keyboard-operable and needs explicit confirmation", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Weaken?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "Weaken" })).toBeDisabled();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
    await user.click(opener);
    await user.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Weaken" })).toBeEnabled();
  });
});

describe("states", () => {
  it("shows access denied for 403 without the raw error", () => {
    render(<ErrorState title="We could not load scans." error={new ApiError(403, "FORBIDDEN", "Your role cannot read scans.")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Access denied");
  });

  it("contains rendering failures", async () => {
    const Broken = () => {
      throw new Error("boom");
    };
    const original = console.error;
    console.error = () => undefined;
    render(<ErrorBoundary><Broken /></ErrorBoundary>);
    console.error = original;
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong.");
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });
});
