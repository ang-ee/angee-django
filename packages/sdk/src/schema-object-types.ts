import {
  isObjectType,
  type GraphQLObjectType,
  type GraphQLSchema,
} from "graphql";

/** Return non-operation object types from a schema, excluding introspection. */
export function schemaObjectTypes(schema: GraphQLSchema): readonly GraphQLObjectType[] {
  const operationTypes = new Set(
    [schema.getQueryType(), schema.getMutationType(), schema.getSubscriptionType()]
      .filter((type): type is GraphQLObjectType => type != null)
      .map((type) => type.name),
  );
  return Object.values(schema.getTypeMap()).filter(
    (type): type is GraphQLObjectType =>
      isObjectType(type)
      && !type.name.startsWith("__")
      && !operationTypes.has(type.name),
  );
}
