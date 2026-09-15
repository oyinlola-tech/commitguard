import { getData } from "./client";
import type { Rule, RuleDetail } from "./types";

export const listRules = (): Promise<Rule[]> => getData<Rule[]>("/rules");
export const getRule = (id: string): Promise<RuleDetail> => getData<RuleDetail>(`/rules/${encodeURIComponent(id)}`);
