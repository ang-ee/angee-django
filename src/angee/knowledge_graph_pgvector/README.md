# Passage retrieval with pgvector

This optional addon adds hybrid passage retrieval to Knowledge. A vault selects
`retrieval_class="pgvector"` and may select an embedding `InferenceModel` through
`embedding_model`. Both are writable through the vault GraphQL resource. Selecting
a model opts that vault's document text into the provider's processing. The writer
must be able to read an available embedding model approved for the
`knowledge_embedding` role. Every provider call rechecks that role's approval.
Readers need access to the pages, without separate inference-catalogue grants.

`search_pages` keeps its existing page result. Knowledge's `search_passages`
GraphQL field and MCP tool return each page's best passage: heading path, text,
exclusive character offsets into the current body, rank and backend-local score.
A lexical vault returns page-only hits. The `related_pages` GraphQL query, Page
field and MCP tool combine current-model embedding-centroid neighbours with
resolved wikilinks. There is no `semantic_search` override of the vault's choice.

## Installation and migration ownership

Enable `angee.knowledge_graph_pgvector` in the host's `INSTALLED_APPS`. Its manifest
requires Knowledge, Agents, MCP and Jobs, plus `pgvector`,
`semantic-text-splitter`, NetworkX, SciPy and PyYAML. SciPy supplies the sparse
matrix backend used by NetworkX's public `pagerank` API. A PostgreSQL server must have the pgvector extension
package installed. SQLite can host the source models for tests but refuses the
pgvector backend selection and search; it provides no fallback.

The addon's native `AppConfig.ready()` connects `pre_migrate`. Before Django
applies migrations, the receiver executes `CREATE EXTENSION IF NOT EXISTS` for both `vector` and `pg_trgm`
on PostgreSQL and does nothing on SQLite. If creation fails, the error identifies
the extension and privileges needed. With a restricted application role, a database
administrator must create the extensions in the database first. Test databases
created without migrations also need both in their template database before table
creation. Provision hooks run after migrations and do not prepare this extension.

Projects own migration history. After composition, the host authors native
migrations with `makemigrations` for:

- `knowledge`: the `Vault.embedding_model`, `extraction_model` and `graph_entity_kinds` donor fields;
- `knowledge_graph_pgvector`: `Passage` (including graph completion markers),
  `PassageEmbedding`, `Entity`, `EntityMention`, `EntityEdge`, their constraints,
  the passage GIN index and the `(model, passage)` btree index.

For portable, explicit migration history, the host may put
`pgvector.django.VectorExtension()` before the vector table's `CreateModel`
operation; the lifecycle receiver makes this idempotent. Reversing that operation
drops the extension, so review reversals where other tables use vectors. The
host can similarly include Django's `TrigramExtension` to prepare the resolver's
SQL functions. The addon ships no competing migration history. Apply host migrations and the normal
REBAC provisioning before restarting the application through its operator.

## Index lifecycle

`Passage` stores the page identity, ordinal, heading path, body coordinates,
content hash, source fingerprint and PostgreSQL search vector. Its text is a
slice of the canonical markdown body. Knowledge's outline supplies section
boundaries; [MarkdownSplitter](https://semantic-text-splitter.readthedocs.io/en/stable/semantic_text_splitter.html)
splits long sections and returns their character offsets. Chunking budgets the
contextual title and heading prefix as well as the body. With no tokenizer in
the inference contract, UTF-8 byte length is the conservative size callback,
capped at 2,000 and at the reported `max_input_tokens()` when available. Duplicate
headings remain addressable by offsets. An empty body yields a title passage.

The manager diffs by `text_hash`: surviving passages keep their primary keys
while ordinal and offsets move; deleted passages cascade their vectors and any
addon-contributed evidence. A model change leaves passage boundaries and old
model vectors intact, and backfills only the newly selected model. Unchanged
passages are never rechunked just because the model budget changes; a deployment
must choose a model able to accept those existing inputs. `PassageEmbedding`
stores one vector per `(passage, model)`, using an any-dimension `VectorField`.
Batches must have finite, nonzero vectors and consistent dimensions for the model.

Page/body saves dispatch `embed_page` through Jobs' commit boundary. Vault saves
dispatch a bounded reconciliation. The periodic reconciler enqueues at most 100
stale markdown pages every ten minutes; it catches raw clones, bulk writes,
lost sends and backfills. Folders carry no body and are not indexed. The embed
task holds a per-page advisory lock and batches at most 32 inputs per provider
call. Database locks never surround provider IO; the write phase rechecks the
source fingerprint, selected model and backend before publishing. Only failures
classified as transient by the provider get task retries. Other failures remain
visible to the worker and the page remains stale for reconciliation.

Trash retains the index for restoration. Queries exclude trashed pages and
vaults, even when an administrator can otherwise read them. Hard page deletion
cascades the index. Stale source fingerprints are excluded from retrieval until
the index catches up, so old coordinates cannot quote newly edited text.

## Retrieval and permissions

`permissions.zed` makes passage reads inherit page reads, and vector reads inherit
passage reads. Every retrieval arm narrows to readable, live pages before ranking:

- Exact cosine distance in the vault's currently selected model space. Each
  model embeds the query once for its vault group; distances from different
  models are never compared.
- PostgreSQL full-text `websearch` over passages, using the language-neutral
  `simple` configuration and cover-density ranking. The base-owned search stamp
  also serves messaging and bounds PostgreSQL's text input.
- One-hop resolved Knowledge links from the initial hybrid seeds. Both endpoints
  must be readable. Outgoing edges carry the seed's RRF contribution; incoming
  edges carry half. The expansion does not recurse.
- The opt-in local entity graph described below, seeded by the initial hybrid
  passages and entity names matching the query.

Each arm keeps its best passage per page. Reciprocal rank fusion uses `k=60`,
with final ties on `(title, sqid)`. Candidate overfetch is four times the requested
page count, bounded by Knowledge's 100-page maximum. Provider failures surface as
GraphQL/MCP errors; full-text-only operation is intentional only when no model
is selected. Related pages need no query embedding call.

Search uses [pgvector's exact operators](https://github.com/pgvector/pgvector-python#django).
A deployment can later author partial expression HNSW indexes per model and
dimension, with matching casts in its queries. The addon does not install an
approximate index or mix vector spaces. Extracted entity graphs are a separate
opt-in capability; the structural link arm needs no LLM or additional tables.

## Passage entity graph

Set a vault's `extraction_model` to an available CHAT `InferenceModel` to enable
extraction and local graph retrieval. The default is null (off). The vault writer
must be able to read the model, and its exact deployment must be admitted under
`ANGEE_INFERENCE_APPROVED_DEPLOYMENTS.knowledge_graph_extraction` when a deployment
approval policy is configured. Both selection and each paid call enforce that
role. This includes optional gleaning and entity adjudication. Selecting a model
authorizes that vault's passages to be sent to its provider.

The vault's `graph_entity_kinds` restricts the structured output schema; defaults
are person, organization, place, concept and event. The model and kinds are
writable through the vault GraphQL resource. `PassageGraph` defines bounded entity
and edge lists, with edge endpoints referring to entries in the same response.
Inference uses Agents' native `infer(output_schema=...)`, followed by Pydantic
validation. Document text is passed as untrusted evidence.

`Entity` stores only `(vault, key, name, kind)`, unique by `(vault, key, kind)`.
`EntityMention` and `EntityEdge` retain descriptions against one passage. Each
relationship has one row per supporting passage, and weights combine at query
time. There are no stored entity summaries or community reports.

Entity reads require both vault read access and a live readable mention. A native
field-backed reverse relation enforces this existential rule for list reads,
relation reads and point checks. Mention and edge reads inherit passage access
through an untrashed page and vault. Even a page deleter who can inspect trash
cannot see graph facts supported only by trash. Page-only sharing does not grant
the entity's vault permission floor.

Extraction is dispatched only by the reconciler's `graph_stale()` selection,
after the page has been idle for five minutes. A tick enqueues at most 100 graph
passages in addition to the embedding batch. `extract_passage` takes a per-passage
task lock; provider IO is outside database transactions. Publication rechecks the
source, idle time, vault, model, vocabulary and current deployment approval under
locks. A successful empty graph is marked complete too. Hash-preserving passage
diffs retain graph rows; model or kind changes become stale and re-extract in
bounded ticks. Stale source/model/vocabulary evidence is excluded from local
queries immediately. Changing back to a previously replaced extraction model
requires extraction again; vectors retain their separate per-model cache.
Gleaning and adjudication setting changes apply to the next eligible extraction;
they do not invalidate completed passages.

Resolution is vault-local: Unicode NFKC, casefold and whitespace normalization
produce the exact key, then PostgreSQL `TrigramSimilarity` considers only the
same kind. A fuzzy match needs similarity at least 0.85 and a 0.05 lead over the
next candidate. Ambiguous names remain distinct unless LLM adjudication is
enabled; adjudication can only choose among the supplied candidates. The
reconciler also deletes at most 100 mention-less entities per tick, sharing the
publisher's vault lock so collection cannot race a new mention. Trash retains
evidence for restoration; passage deletion cascades it.

Deployment overrides live in `ANGEE_KNOWLEDGE_GRAPH`; omitted keys use the
validated defaults in `graph_config.GraphOptions`:

```yaml
ANGEE_KNOWLEDGE_GRAPH:
  idle_minutes: 5
  gleaning_passes: 0      # only 0 or 1 is accepted
  llm_adjudication: false
  trigram_threshold: 0.85
  hops: 2                # only 1 or 2 is accepted
  degree_cap: 20
  seed_limit: 20
  node_limit: 400
```

Local mode seeds entities in the top hybrid evidence passages and matching
names. Each hop prefilters the evidence and both endpoints under the caller,
then limits supporting edge rows per frontier entity. The in-memory graph is
also node-bounded. It traverses relationships in either direction, retaining each
supporting passage as a separate edge. [NetworkX personalized PageRank](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.link_analysis.pagerank_alg.pagerank.html)
ranks this visible subgraph. Reached entity mentions and traversed edges rank
their evidence passages; each entity contributes at most `degree_cap` mention
rows. A passage can be returned for mentioning a reached entity without its
other edges being traversed. Page and passage ties are deterministic. Queries
make no graph inference calls. This ranking is an additional RRF arm; disabling
the extraction model immediately removes it from search.

## Retrieval evaluation

Run `knowledge_retrieval_eval` with the composed host's `manage.py`, an active
user login, readable pgvector vault public IDs and an authored golden file:

```shell
python manage.py knowledge_retrieval_eval golden.yaml --actor reader@example.com --vault vlt_...
```

The command reads YAML or JSON, resolves expected titles under that actor, and
refuses missing, trashed, unreadable or ambiguous titles before making provider
calls. It does not create pages, extract graphs or backfill vectors. Let the
reconciler finish indexing before measurement. Vector evaluation makes the same
query embedding calls as normal retrieval; each model is called once per query.

```yaml
cases:
  - query: Which team builds the sensor used by Orion?
    expected_pages: [Orion design, Lyra sensor, Atlas team]
    category: multi_hop
```

JSON output reports mean recall@10 and MRR over the bounded ranking of up to
100 distinct pages, overall and separately for `factoid` and `multi_hop` cases.
An expected page outside the returned ranking contributes zero reciprocal rank.
Arms are `vector`, `lexical`,
`hybrid`, `hybrid+G0`, and `hybrid+G1`. These are cumulative: the last includes G0
and the entity arm and is exactly the production search ranking. A disabled or
unbuilt graph cannot improve that arm. All five share the production arm methods
and the same candidate budget.

[`evaluation/golden.json`](evaluation/golden.json) is a small synthetic starter
set, not measured evidence from a real vault. Its matching corpus is:

| Page | Grounded facts |
| --- | --- |
| Orion launch | Orion launches on 15 June. |
| Orion design | Orion uses the Lyra sensor. |
| Lyra sensor | The Atlas team builds Lyra. |
| Atlas team | Mira leads Atlas. |
| Atlas location | Atlas is based in Quito. |

Replace or expand the starter with approved questions and expected pages from
the deployment's vaults before deciding whether to enable graph extraction.
This addon provides the measurement path; it does not claim a measured quality
gain. Global/thematic graphs and optional graph-browsing MCP tools remain outside
the local retrieval contract.
