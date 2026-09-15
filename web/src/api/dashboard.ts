import { getData } from "./client";
import type { Overview } from "./types";

export type Period = "24h" | "7d" | "30d";

export const getOverview = (period: Period, organization?: number | null): Promise<Overview> =>
  getData<Overview>("/dashboard/overview", { period, organization });
