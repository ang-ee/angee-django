import { graphql } from "@angee/gql/console";
import type { TypedDocumentNode } from "@angee/refine";

export interface PortfolioUpdateActionResult {
  ok: boolean;
  message: string;
  id: string | null;
  validation_errors: Record<string, string[]> | null;
}

export interface PortfolioUpdateVariables extends Record<string, unknown> {
  id: string;
  health: "ON_TRACK" | "AT_RISK" | "OFF_TRACK";
  body: string;
}

export const ReportProjectUpdateDocument = graphql(`
  mutation PortfolioReportProjectUpdate(
    $id: ID!
    $health: UpdateHealth!
    $body: String!
  ) {
    report_project_update(id: $id, health: $health, body: $body) {
      ok
      message
      id
      validation_errors
    }
  }
`) as TypedDocumentNode<
  { report_project_update: PortfolioUpdateActionResult },
  PortfolioUpdateVariables
>;

export const ReportInitiativeUpdateDocument = graphql(`
  mutation PortfolioReportInitiativeUpdate(
    $id: ID!
    $health: UpdateHealth!
    $body: String!
  ) {
    report_initiative_update(id: $id, health: $health, body: $body) {
      ok
      message
      id
      validation_errors
    }
  }
`) as TypedDocumentNode<
  { report_initiative_update: PortfolioUpdateActionResult },
  PortfolioUpdateVariables
>;
