"""Machine-readable descriptions of Angee product data surfaces.

The contract builds only on the framework core and is consumed by multiple
addons, including the GraphQL engine and MCP tools. It imports neither Strawberry
nor :mod:`angee.graphql`. It describes a surface only: producers that derive
descriptions from Django or transport-specific projections live with those
projections. Contract vocabulary and wire-envelope naming live here; projection
defaults such as the public-id model field are supplied by producers.
"""
