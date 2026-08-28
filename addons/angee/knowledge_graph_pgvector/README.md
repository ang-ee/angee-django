# knowledge_graph_pgvector (plugin skeleton)

A **semantic-retrieval plugin** for the knowledge addon. Its job here is to prove
that graph-RAG / pgvector capability bolts onto `angee.knowledge` through its
declared seams alone — **with zero edits to the knowledge addon**. The bundled
backend is a lexical stub; the real embedding model, its backfill, and the ANN
index are this plugin's responsibility (see *Out of scope*).

It is intentionally **not** in any project's `INSTALLED_APPS` — it is the
template/contract for a real semantic backend, verified by
`tests/test_knowledge_plugin.py`.

## The three seams (each owned by a base addon)

### 1. Retrieval backend — the `ImplClassField` registry

`knowledge.Vault.retrieval_class` is an `ImplClassField` over the
`ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES` registry (default `lexical`). A plugin
contributes one impl with a **namespaced key** through the autoconfig dotted-key
deep-merge — mirroring `agents_integrate_anthropic`:

```python
# autoconfig.py
SETTINGS = {
    "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES.pgvector":
        "angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend",
}
```

```python
# retrieval.py — a concrete RetrievalBackend leaf (here a lexical stub)
class PgvectorRetrievalBackend(LexicalRetrievalBackend):
    key = "pgvector"
```

A vault then selects it with `retrieval_class = "pgvector"`, and knowledge's
`search_pages` resolver dispatches to it via the vault's `retrieval` property;
this plugin's `semantic_search` forces its own key via the public
`Vault.retrieval_for("pgvector")` seam — no edit to the knowledge addon and no
reach into its model internals. The field guarantees deterministic enum order
(sorted keys) and fail-fast `manage.py check` validation of every impl path.
Keys are **last-writer-wins** on collision, so always namespace the key to the
plugin (`pgvector`, `graphrag`).

> Override vs augment: `ImplClassField` selects exactly **one** backend per
> vault. To run lexical + semantic and fuse, ship a `RetrievalBackend` subclass
> that resolves each arm through `Vault.retrieval_for(key)` and blends their
> results in `search` — fan-out lives on the impl, not in a list-merge seam.

### 2. GraphQL — projection extension + new query (`schemas`)

The `schemas` seam — inferred from this addon's `schema.py` (which defines
`schemas`) — contributes into knowledge's existing `public`/`console` buckets (the
composer merges per bucket):

- **`type_extensions`** — `@strawberry_django.type(Page, name="PageType",
  extend=True)` adds `related_pages` onto knowledge's `PageType` (mirrors
  `iam_integrate_oidc`'s `OAuthClientOidcExtension`).
- **`query`** — a `semantic_search` root field that forces this plugin's provider
  through the vault-owned registry.

### 3. MCP tools — the `mcp_tools` register seam

The `mcp_tools` seam — inferred from this addon's `mcp_tools.py` (which defines
`register`) — adds a `semantic_search` `GraphQLTool` over the
query above. It runs the same actor-scoped GraphQL engine the knowledge tools use,
so REBAC scoping and projection are reused — the tool only names the operation and
its projection. The `/mcp` mount auto-lights via `has_tools()`.

## Adding a real backing column

A production plugin that stores embeddings contributes an abstract source model
with `extends = "knowledge.Page"` carrying the embedding (pgvector) field. The
composer folds it into the runtime `Page`; field-name collisions across addons
**fail fast** at compose time. That column, its backfill, the pgvector extension,
and the ANN index ship as **this plugin's migration** — never the knowledge
addon's.

## Out of scope (this skeleton)

Real embeddings, a vector/graph index, the `knowledge.Page` embedding column and
its migration, and ranking by vector distance. The seams above are exactly what
make those drop-in: the provider key, the projection/query buckets, and the MCP
tool register call.
