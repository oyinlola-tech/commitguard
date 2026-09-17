import { getData, send } from "./client";
import { org } from "./governance";
import type { OrganizationRuleDocument, OrganizationRules, OrganizationRuleVersion } from "./types";

export const getOrganizationRules = (organization: number): Promise<OrganizationRules> => getData<OrganizationRules>(`${org(organization)}/rules`);
export const listOrganizationRuleVersions = (organization: number): Promise<OrganizationRuleVersion[]> =>
  getData<OrganizationRuleVersion[]>(`${org(organization)}/rules/history`);
export const updateOrganizationRules = (organization: number, expectedVersion: number, rules: OrganizationRuleDocument, reason: string) =>
  send<OrganizationRules>("PUT", `${org(organization)}/rules`, { expected_version: expectedVersion, rules, reason }).then((r) => r.data);
