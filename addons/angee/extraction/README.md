# Extraction evidence

Extraction retains immutable source, page and carrier rows together with a
schema candidate. A lineage foreign key groups revisions, and a tagged `outcome`
records success or an explicit failure code with grounding facts. Exactly one
protected `file` or `message` target grants inherited read access through its
field relation. Each source is a base `DerivedFrom` row with a canonical record
reference; its explicit file or message-part link keeps part reads intersected
with source read access. The base evidence admission check authorizes the source
set at retention and again before inference. Profiles supply interpretation through `ExtractionProfile`;
`ExtractionManager.prepare_pages` acquires bytes, and recognition and mapping
use `agents.InferenceModel.infer`. Consumers hold a protected reference to
`Extraction` and read documents, lines and facts through
its typed accessors. An absent fact raises `KeyError`; a retained JSON null stays
`None`. Candidates awaiting identity correspondence cannot be read by identity.

The `workflows_extraction` adapter's `document_extraction` definition prepares
pages once, maps recognition over raster pages, collects partial results, and
retains processed evidence or a source hold. `infer_evidence` consumes that
snapshot, using a pinned revision and explicit identity correspondence when
structure changes. Retention
serializes revision allocation on the lineage head and checks exact request reuse.
Inference preserves source-grounded facts, correction authority and retained
carriers. Revisions reference the same digest-bound part files instead of copying
the full carrier value into each part row. It rechecks source access before model work; explicit correspondence
finalizes the held candidate without another inference call.

Acquisition and inference run as IO steps with fenced heartbeats, declared retry
policy and retained artifacts. Non-idempotent inference marks its effect before
calling the model. Preparation and recognition store carrier contents in
protected files; step payloads carry references and digests. File access is
checked again when a later step reads them.

Profiles, schemas, acquisition bounds and model request settings belong to
author-controlled node config. `ExtractionProfile.parse_config` and frozen
Pydantic config models validate declarations at publication, including the
registered profile, its settings and the versioned object schema. Run input supplies record
references and revision correspondence. The shipped definition declares the
built-in plain text profile and schema.

The shipped definition runs prepare, a `map` of page recognition, then process.
Processing consumes the engine's typed `MapItem` results directly; the map's
`failed` edge also routes to processing, so a failed page becomes a source hold
while its siblings' evidence is retained.

Register profiles through `ANGEE_EXTRACTION_PROFILE_CLASSES`. The domain manifest
owns native parser dependencies. Historical `Document*` contracts remain aliases to the pure
contracts. Decision-backed corrections compose the typed resolution contract in
`angee.decisions`; [`ExtractionManager`](managers.py) owns revision and reviewed
authority validation. [`schema.py`](schema.py) exposes read-only evidence through
the framework resource owner and actor-scoped relations.
