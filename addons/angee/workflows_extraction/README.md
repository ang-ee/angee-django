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

The current engine has no IO mode or map execution. `# L2` marks the temporary
DATABASE declaration and pending context hooks; native providers fail closed
under that declaration. Tests register deterministic providers through the same
`ImplBase` registry. The shipped map definition awaits L4 installation support.
Owned-child admission belongs to the engine's later start-run contract.

Register profiles through `ANGEE_EXTRACTION_PROFILE_CLASSES` and providers through
`ANGEE_EXTRACTION_PROVIDER_CLASSES`. The manifest owns installation and native
parser dependencies. `profiles.authored_profile_config` replaces the deleted
service helper; historical `Document*` contracts remain aliases to the pure
contracts. Decision-backed correction writes will compose the new decisions
owner when it is available; the removed workflow decision API is not retained.
