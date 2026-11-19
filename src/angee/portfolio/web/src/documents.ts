import { graphql } from "@angee/gql/console";

export const PromoteProjectToProductDocument = graphql(`
  mutation PortfolioPromoteProjectToProduct($id: ID!) {
    promote_project_to_product(id: $id) { ok message id code validation_errors }
  }
`);
