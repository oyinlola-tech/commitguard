import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/public-sans";
import "@fontsource/commit-mono/400.css";
import "@fontsource/commit-mono/600.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/app.css";
import "./styles/public.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { AppRoutes } from "./App";
import { ApiError } from "./api/client";
import { applyTheme, readTheme } from "./lib/preferences";

applyTheme(readTheme());

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Security state must not look fresher than it is: short staleness, refetch on focus.
      staleTime: 10_000,
      refetchOnWindowFocus: true,
      retry: (failures, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failures < 2,
    },
    mutations: { retry: false },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
