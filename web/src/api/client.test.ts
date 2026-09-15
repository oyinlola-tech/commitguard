import { describe, expect, it, vi } from "vitest";

import { ApiError, UNAUTHENTICATED_EVENT, buildUrl, getPage, send, setCsrfToken } from "./client";
import { apiError, mockFetch, page } from "../test/render";

describe("API client", () => {
  it("sends the CSRF token on writes and never on reads", async () => {
    const fetch = mockFetch();
    fetch.on("PUT", "/api/v1/violations/x/acknowledgement", { body: { data: {}, meta: {} } });
    fetch.on("GET", "/api/v1/scans", page([]));
    setCsrfToken("token-123");
    await send("PUT", "/violations/x/acknowledgement", { note: null });
    await getPage("/scans");
    const write = fetch.calls.find((c) => c.method === "PUT");
    const read = fetch.calls.find((c) => c.path === "/api/v1/scans");
    expect(write?.headers.get("X-CSRF-Token")).toBe("token-123");
    expect(write?.headers.get("Content-Type")).toBe("application/json");
    expect(read?.headers.get("X-CSRF-Token")).toBeNull();
    setCsrfToken(null);
  });

  it("normalises error envelopes", async () => {
    const fetch = mockFetch();
    fetch.on("GET", "/api/v1/scans", apiError(403, "FORBIDDEN", "No access"));
    const error = await getPage("/scans").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: "FORBIDDEN", message: "No access", requestId: "req-1" });
  });

  it("broadcasts expired sessions exactly once per request", async () => {
    const fetch = mockFetch();
    fetch.on("GET", "/api/v1/scans", apiError(401, "SESSION_EXPIRED"));
    const listener = vi.fn();
    window.addEventListener(UNAUTHENTICATED_EVENT, listener);
    await getPage("/scans").catch(() => undefined);
    window.removeEventListener(UNAUTHENTICATED_EVENT, listener);
    expect(listener).toHaveBeenCalledTimes(1);
    expect((listener.mock.calls[0]![0] as CustomEvent).detail).toBe("SESSION_EXPIRED");
  });

  it("does not treat re-authentication as a lost session", async () => {
    const fetch = mockFetch();
    fetch.on("PUT", "/api/v1/policies/1001", apiError(401, "REAUTHENTICATION_REQUIRED"));
    const listener = vi.fn();
    window.addEventListener(UNAUTHENTICATED_EVENT, listener);
    await send("PUT", "/policies/1001", {}).catch(() => undefined);
    window.removeEventListener(UNAUTHENTICATED_EVENT, listener);
    expect(listener).not.toHaveBeenCalled();
  });

  it("reports network failures without leaking details", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch 10.0.0.1"); }));
    const error = await getPage("/scans").catch((e: unknown) => e);
    expect(error).toMatchObject({ code: "NETWORK_ERROR" });
    expect((error as Error).message).not.toContain("10.0.0.1");
  });

  it("encodes query parameters and drops empty ones", () => {
    expect(buildUrl("/scans", { q: "a&b=c", result: "", cursor: null, limit: 25 })).toBe("/api/v1/scans?q=a%26b%3Dc&limit=25");
  });
});
