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
requires Knowledge, Agents, MCP and Jobs, plus `pgvector` and
`semantic-text-splitter`. A PostgreSQL server must have the pgvector extension
package installed. SQLite can host the source models for tests but refuses the
pgvector backend selection and search; it provides no fallback.

The addon's native `AppConfig.ready()` connects `pre_migrate`. Before Django
applies migrations, the receiver executes `CREATE EXTENSION IF NOT EXISTS vector`
on PostgreSQL and does nothing on SQLite. If creation fails, the error identifies
the extension and privileges needed. With a restricted application role, a database
administrator must create the extension in the database first. Test databases
created without migrations also need it in their template database before table
creation. Provision hooks run after migrations and do not prepare this extension.

Projects own migration history. After composition, the host authors native
migrations with `makemigrations` for:

- `knowledge`: the `Vault.embedding_model` donor field;
- `knowledge_graph_pgvector`: `Passage`, `PassageEmbedding`, their unique
  constraints, the passage GIN index and the `(model, passage)` btree index.

For portable, explicit migration history, the host may put
`pgvector.django.VectorExtension()` before the vector table's `CreateModel`
operation; the lifecycle receiver makes this idempotent. Reversing that operation
drops the extension, so review reversals where other tables use vectors. The
addon ships no competing migration history. Apply host migrations and the normal
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
