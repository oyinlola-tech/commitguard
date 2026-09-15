import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { vi } from "vitest";

import { AppRoutes } from "../App";
import type { SessionInfo } from "../api/types";
import { session as makeSession } from "./fixtures";

export interface MockResponse {
  status?: number;
  body?: unknown;
}

type Handler = (request: { method: string; url: URL; body: unknown; headers: Headers }) => MockResponse | undefined;

export interface FetchMock {
  calls: { method: string; path: string; search: string; body: unknown; headers: Headers }[];
  on: (method: string, path: string | RegExp, response: MockResponse | ((req: { url: URL; body: unknown }) => MockResponse)) => void;
}

/** A routed `fetch` replacement: the dashboard's only network dependency. */
export function mockFetch(sessionInfo: SessionInfo | null = makeSession()): FetchMock {
  const routes: { method: string; path: string | RegExp; handler: Handler }[] = [];
  const calls: FetchMock["calls"] = [];
  const mock: FetchMock = {
    calls,
    on(method, path, response) {
      routes.unshift({ method, path, handler: (req) => (typeof response === "function" ? response(req) : response) });
    },
  };
  mock.on("GET", "/health", { status: 200, body: { status: "ok" } });
  mock.on("GET", "/api/v1/auth/session", sessionInfo ? { body: { data: sessionInfo, meta: {} } } : { status: 401, body: { error: { code: "UNAUTHENTICATED", message: "Sign in to continue.", request_id: "r" } } });
  mock.on("GET", "/api/v1/dashboard/overview", { status: 404, body: { error: { code: "NOT_FOUND", message: "x", request_id: "r" } } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input, "http://localhost");
      const method = (init.method ?? "GET").toUpperCase();
      const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
      const headers = new Headers(init.headers);
      calls.push({ method, path: url.pathname, search: url.search, body, headers });
      const route = routes.find((r) => r.method === method && (typeof r.path === "string" ? r.path === url.pathname : r.path.test(url.pathname)));
      const response = route?.handler({ method, url, body, headers }) ?? { status: 404, body: { error: { code: "NOT_FOUND", message: "The requested resource was not found.", request_id: "r" } } };
      return new Response(JSON.stringify(response.body ?? {}), { status: response.status ?? 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return mock;
}

export const page = <T,>(items: T[], next: string | null = null) => ({ body: { data: items, meta: { next_cursor: next, limit: 25 } } });
export const data = (value: unknown, meta: Record<string, unknown> = {}) => ({ body: { data: value, meta } });
export const apiError = (status: number, code: string, message = "Error") => ({ status, body: { error: { code, message, request_id: "req-1" } } });

export function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

export function renderApp(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export function renderWithProviders(ui: ReactElement, path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}
