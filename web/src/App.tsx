import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";

import { RequireSession } from "./auth/session";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AppShell } from "./components/layout/AppShell";
import { LoadingState } from "./components/States";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";

const Overview = lazy(() => import("./pages/Overview"));
const Repositories = lazy(() => import("./pages/Repositories"));
const RepositoryDetail = lazy(() => import("./pages/RepositoryDetail"));
const Scans = lazy(() => import("./pages/Scans"));
const ScanDetail = lazy(() => import("./pages/ScanDetail"));
const Violations = lazy(() => import("./pages/Violations"));
const ViolationDetail = lazy(() => import("./pages/ViolationDetail"));
const Policies = lazy(() => import("./pages/Policies"));
const Rules = lazy(() => import("./pages/Rules"));
const RuleDetail = lazy(() => import("./pages/RuleDetail"));
const Audit = lazy(() => import("./pages/Audit"));
const Installations = lazy(() => import("./pages/Installations"));
const InstallationDetail = lazy(() => import("./pages/InstallationDetail"));
const Settings = lazy(() => import("./pages/Settings"));
const Notifications = lazy(() => import("./pages/Notifications"));

export function AppRoutes() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<LoadingState label="Loading…" fullPage />}>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route element={<RequireSession />}>
            <Route element={<AppShell />}>
              <Route path="/dashboard" element={<Overview />} />
              <Route path="/repositories" element={<Repositories />} />
              <Route path="/repositories/:repositoryId" element={<RepositoryDetail />} />
              <Route path="/scans" element={<Scans />} />
              <Route path="/scans/:scanId" element={<ScanDetail />} />
              <Route path="/violations" element={<Violations />} />
              <Route path="/violations/:violationId" element={<ViolationDetail />} />
              <Route path="/policies" element={<Policies />} />
              <Route path="/policies/:organizationId" element={<Policies />} />
              <Route path="/notifications" element={<Notifications />} />
              <Route path="/rules" element={<Rules />} />
              <Route path="/rules/:ruleId" element={<RuleDetail />} />
              <Route path="/audit" element={<Audit />} />
              <Route path="/github" element={<Navigate to="/github/installations" replace />} />
              <Route path="/github/installations" element={<Installations />} />
              <Route path="/github/installations/:installationId" element={<InstallationDetail />} />
              <Route path="/settings" element={<Settings />} />
            </Route>
          </Route>
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
