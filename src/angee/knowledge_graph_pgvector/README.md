# Passage retrieval with pgvector

This optional addon adds hybrid passage retrieval to Knowledge. A vault selects
`retrieval_class="pgvector"` and can select an embedding model and an extraction
model. Selecting either model authorizes document egress to that deployment.
The writer needs model read access on a changed selection; each paid call and
publication rechecks the role approval and model capability through Agents.
The role names live in [constants](constants.py) and [extraction](extraction.py).
Readers need page access without separate inference-catalogue grants.

Knowledge's `search_passages` GraphQL field and MCP tool return `hits` and
`failures`. Each hit has a page, heading path, text, exclusive character offsets,
rank and backend-local score. A failed vector arm is dropped for its model group;
`failures` identifies the affected readable vaults without exposing provider
errors. Lexical, link and enabled graph arms continue. Query embeddings use
Django's cache keyed by model and query, after approval checks. The page-only
`search_pages` projection remains available. The root `related_pages(page)` query
and MCP tool combine current-model centroids with readable wikilinks.

## Database and migration ownership

Enable `angee.knowledge_graph_pgvector` in the host's `INSTALLED_APPS`. Its
[manifest](pyproject.toml) owns the dependencies: pgvector-python,
semantic-text-splitter, NetworkX, SciPy (NetworkX's sparse PageRank backend),
and PyYAML (evaluation input), plus the Angee owners it composes.

PostgreSQL must have pgvector installed on the server. The native
[pre_migrate receiver](signals.py) is the single extension-creation owner for
`vector` and `pg_trgm`; it is a no-op on SQLite. A restricted role needs an
administrator to install `vector` in the database before migrations. `pg_trgm`
is trusted on supported PostgreSQL versions and needs database CREATE privilege.
The [test prerequisites](../../../docs/checks.md#local-postgresql-prerequisites)
prepare template1 because `--nomigrations` bypasses pre_migrate. SQLite refuses
pgvector selection and retrieval.

Hosts generate their own native migrations after composition. The source models
own the vault selection fields, page index state, passages, per-model embeddings,
entities, mention surface names, edges, constraints and indexes. The entity key
uses a trigram GIN index; passage search uses a full-text GIN index. Regenerate
host migrations for these declarations; addon source contains no host artifacts.

## Incremental indexing

[MarkdownPage.sections](../knowledge/models.py) owns outline spans and heading
paths. `PassageManager.rebuild_for` composes MarkdownSplitter, then bulk-diffs
passages by contextual text hash. Reordered or unchanged text retains evidence
identity; removed passages cascade their vectors and graph evidence. A vault
move replaces passages so old-vault graph identities cannot survive the move.
Source freshness compares Knowledge's stored body hash and title, plus vault
identity, without hashing bodies during reads. Knowledge also maintains body
hashes on bulk writes.

Rebuilding precedes provider admission, so lexical passages remain available
without an approved embedding model. Discovery supplies the input budget;
unknown budgets use the [index constants](constants.py). Agents owns finite,
nonzero vector validation, dimension admission and normalized usage events.
`InferenceBackend.embed` owns native provider binding and client lifetime.

Page saves enqueue only for pgvector vaults. The [tasks](tasks.py) use Jobs'
commit boundary and shared advisory locks; duplicate page tasks wait for the
current task, and changed snapshots are re-enqueued. Provider calls occur outside
transactions. Publication uses no-key source/selection locks and an unlocked
Agents approval recheck. MCP read operations likewise execute outside an outer
transaction.

[IndexState](models.py) stores failures on their owning rows: the page for
indexing, the passage for graph extraction. The marker includes the failed input
and selection, attempt count, safe error type and next attempt time. Transient
failures use integrate's doubling backoff; deterministic invalid output completes
that input with an error. Changed inputs become eligible immediately. This keeps
poison work from monopolizing each bounded reconciliation batch. The reconciler
also collects orphan entities and vectors for models no pgvector vault selects.
Batch sizes, cadence and query-cache lifetime live in [constants](constants.py).

Trash retains evidence for restoration, while every retrieval arm excludes
trashed pages and vaults. Hard deletion cascades evidence; embedding deletion
also admits the owning model's delete permission.

## Retrieval and graph evidence

The [retrieval backend](retrieval.py) combines exact cosine ranking per model,
PostgreSQL websearch with the simple configuration, resolved one-hop wikilinks,
and optional local entity evidence. It scopes pages and both link endpoints
before ranking. Reciprocal rank fusion keeps one best passage per page; ties
use title and database page identity consistently across SQL and fusion.
Centroids are explicitly converted from pgvector's native Python representation
before ranking. Related pages require no provider call.

Selecting a CHAT extraction model opts into schema-guided passage extraction.
The sorted vault vocabulary builds a native Pydantic schema. Empty graphs are
completed work. Unchanged passage hashes preserve graph evidence; model or
vocabulary changes invalidate it. Extraction waits for the configured idle
period and rechecks the source, selection, vocabulary and approval at publication.

`Entity` stores a normalized key and kind within one vault. `EntityMention`
stores each passage's surface name and description. Name seeding uses only
readable, current mentions and phrase tsqueries with word boundaries. Entity
reads require vault access and live readable evidence; mention and edge reads
inherit live passage access. A spelling from an unreadable mention is never
projected as another page's name.

Resolution batches exact keys, then uses PostgreSQL's indexed `%` operator and
TrigramSimilarity within one vault and kind. The calibrated threshold and
adjudication options live in [GraphOptions](options.py); the minimum lead lives
in [constants](constants.py). Ambiguous candidates remain distinct unless optional
adjudication selects an existing candidate. [Resolution tests](../../../tests/test_knowledge_graph_pgvector_resolution.py)
record PostgreSQL's padded, unique-trigram similarity calculations.

Local retrieval composes NetworkX personalized PageRank over readable evidence,
with bounded seeds, hops, node counts and degree. Traversal excludes previously
seen edges and loads only ranking columns. A reached entity's other readable
mentions may supply evidence even when their edges were not traversed.
No graph inference runs during a query. `ANGEE_KNOWLEDGE_GRAPH` overrides
[GraphOptions](options.py), validated by a native Django system check.

## Evaluation

Run the host's `knowledge_retrieval_eval golden.yaml --actor reader@example.com
--vault vlt_...` management command. It resolves an active actor by natural key,
validates readable expected page titles, and evaluates the production vector,
lexical, hybrid, hybrid+G0 and hybrid+G1 rankings. JSON output contains recall@10,
MRR, category aggregates and visible failures. It does not mutate the index.

[fixtures/golden.json](fixtures/golden.json) is a synthetic starter. Its corpus
uses Orion launch, Orion design, Lyra sensor, Atlas team and Atlas location pages.
Replace it with approved deployment questions and expected titles before judging
retrieval quality; the fixture is not evidence of a measured quality gain.
