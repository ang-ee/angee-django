# GraphQL

GraphQL composes ordinary Django models and native Strawberry schema buckets.
It owns resource metadata, public IDs, Hasura CRUD, validation/error sanitization,
HTTP/WebSocket serving and one model-change stream. IAM is optional.

Declare `schemas` in the owning addon's conventional `schema.py`; use
`hasura_model_resource` for native model CRUD or compose Strawberry declarations
directly. Native model fields and querysets own decoding, validation and data.

When IAM is selected, its autoconfig supplies native schema/view/consumer
subclasses and typed resource/event contributions through `angee.base.hooks`.
Protected APIs import `angee.iam.graphql` permission helpers and `ProtectedNode`.
Neutral APIs use `AngeeNode`, normal Django querysets and ordinary
`strawberry_django` relation fields. The selected write backend is wrapped by
provenance's existing adapter when provenance is selected.

Optional-single constructor/subscriber settings reject multiple providers; ordered
resource contributors reject competing native slots. No provider registry or
permission facade is needed. IAM owns its permission SQL and live delivery
checks; GraphQL keeps one native resource builder, persistence backend, collector
and stream.
