# Extraction evidence

Extraction retains immutable source, page and carrier rows together with a
schema candidate. Profiles supply interpretation through `ExtractionProfile`;
providers acquire bytes and call the agents inference owner. Consumers hold a
protected reference to `Extraction` and read documents, lines and facts through
its typed accessors. An absent fact raises `KeyError`; a retained JSON null stays
`None`. Candidates awaiting identity correspondence cannot be read by identity.

The `document_extraction` definition prepares pages once, maps recognition over
raster pages, collects partial results, and retains processed evidence or a
source hold. `infer_evidence` consumes that retained snapshot, using a pinned
revision and explicit identity correspondence when structure changes. Retention
serializes revision allocation on the lineage head and checks exact request reuse.
Inference preserves source-grounded facts, correction authority and retained
carriers. It rechecks source access before provider work; explicit correspondence
finalizes the held candidate without another inference call.

Acquisition and inference run as IO steps with fenced heartbeats, declared retry
policy and retained artifacts. Non-idempotent inference marks its effect before
calling the provider. Preparation and recognition store carrier contents in
protected files; step payloads carry references and digests. File access is
checked again when a later step reads them.

Profiles, schemas, acquisition bounds and model request settings belong to
author-controlled node config. `ImplBase.parse_config` validates the selected
profile and backend declarations. Run input supplies record references and
revision correspondence. The shipped definition defaults to the unconfigured
profile; an author must select a profile before executing it.

The shipped definition runs prepare, a `map` of page recognition, then process.
Processing consumes the engine's typed `MapItem` results directly; the map's
`failed` edge also routes to processing, so a failed page becomes a source hold
while its siblings' evidence is retained.

Register profiles through `ANGEE_EXTRACTION_PROFILE_CLASSES` and providers through
`ANGEE_EXTRACTION_BACKEND_CLASSES`. The manifest owns installation and native
parser dependencies. Historical `Document*` contracts remain aliases to the pure
contracts. Decision-backed corrections compose the typed resolution contract in
`angee.decisions`; [`ExtractionManager`](managers.py) owns revision and reviewed
authority validation. [`schema.py`](schema.py) exposes read-only evidence through
the framework resource owner and actor-scoped relations.
