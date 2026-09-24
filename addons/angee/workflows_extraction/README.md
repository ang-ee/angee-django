# Document extraction

Extraction retains immutable document carriers, schema candidates, source claims,
and revision correspondence. Publications select domain interpretation through an
`ExtractionProfile` registered in `ANGEE_EXTRACTION_PROFILE_CLASSES`.

Profiles own `detect_carriers` and deterministic `process_parts`, `normalize_inference_candidate`,
`inference_required`, `evidence_layout`, and `pipeline_version`. The built-in
`none` profile fails closed: a publication must select a domain profile before it
can interpret evidence. Consumer addons contribute profile registry entries
through autoconfig. Carrier detection receives the retained file snapshot before
native text/image acquisition and returns `DocumentPart` evidence. An empty
result delegates to generic acquisition. Profiles own bounded format parsing and
field mappings; preparation and restoration use the selected profile consistently.
Processing rejects a profile that differs from the retained preparation output.
Older preparation outputs without a profile restore as `none`; unchanged native
carriers remain usable, while carrier changes fail the retained manifest comparison.
Outputs requiring a domain carrier profile must be prepared again before processing.

Recognition and schema mapping use the plain `recognize_page` and `map_text_parts`
functions. They build native requests for `workflows_agents.inference.call_inference`;
that owner checks the admission actor's model access, deployment approval and
capability, classifies provider failures, and debits the run once. Agents owns
provider selection and structured-output decoding.

Every nested operation receives the selected database alias. Document processing
and workflow inference require the default database because the authorization backend cannot
bind access checks to another alias; extraction and the generic inference step
share that fail-closed boundary. Invalid or exhausted request timeouts fail before
provider invocation and are never transient failures.

`Extraction.awaiting_correspondence` identifies a retained candidate whose document
or line identities need review. Callers use that property; retained writes use
`ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED`.

The declared [profile migration](runtime_migrations/extraction_profiles.py) renames
`Extraction.engine` to `profile`, `engine_config` to `profile_config`, and
`ExtractionPage.engine_metadata` to `provider_metadata` before Django's schema
autodetection. Retained JSON payloads and consumer domain keys stay unchanged;
register those keys under `ANGEE_EXTRACTION_PROFILE_CLASSES` instead of
`ANGEE_EXTRACTION_ENGINE_CLASSES`. The old built-in `inference` key selected only
transport and maps to `none`, meaning no domain profile; rollback maps `none`
back to `inference`. An explicitly registered `inference` domain profile retains
that key instead. New interpretation requires an explicit domain profile.
An old consumer engine named `none` conflicts with the new reserved key. The
migration rejects that ambiguity before changing columns; declare a consumer key
migration first so both directions preserve its identity.
Drain retained runs with old input contracts before the cutover, as described in
the [upgrade guidance](../../../docs/backend/guidelines.md#migrations-and-runtime).
