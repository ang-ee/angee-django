# Plan: Knowledge MCP tools (markdown read/update for agents)

Give agents a small, **extensible** tool surface to read and update markdown
pages/notes over MCP — markdown CRUD now, graph-RAG later via a plugin
(`knowledge_graph_pgvector` / `graphrag`). Grounded in a read-only investigation
(scratch notes under the session scratchpad; `md_spike.py` ran clean).

## Goal

Expose knowledge pages to the agent MCP surface with: outline-aware reads,
**backlinks**, content search, and markdown-structure-aware edits
(section-anchored patch, exact-string replace, append/prepend) — every edit
guarded by the existing body-hash CAS and inheriting revisions + backlink
rebuild. The retrieval/search path and the tool set must be **extendable by a
plugin addon without editing the knowledge addon**.

## Architecture Gate

**Owner map.**
- MCP tool exposure → `angee.mcp` (`GraphQLTool` + `register_graphql_tools`,
  `addons/angee/mcp/graphql.py`). Tools run a GraphQL op under the agent's REBAC
  actor; auth/projection reused. Registration seam = `mcp_tools = "<mod>.register"`
  on an `AppConfig` (`mcp/server.py:_registrars`).
- Page identity / tree / CRUD / body CAS / revisions / wikilinks → the knowledge
  addon (`knowledge/models.py`, `schema.py`, `signals.py`). `MarkdownPageManager.
  write_body(expected_hash=)` is the CAS owner; `Link` + `PageType.backlinks` own
  wikilinks; `RevisionMixin` owns history.
- Markdown structure (outline, section boundaries, splice) → a new pure-text
  owner in the knowledge addon, backed by **markdown-it-py** (block token `.map`
  line spans). One owner shared by the `outline` read field and the patch write.
- Pluggable retrieval → a new `RetrievalProvider(ImplBase)` selected by an
  `ImplClassField` registry (`ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES`), default
  `lexical`. Mirrors `agents.InferenceBackend` / `storage.Backend`.

**Sibling inventory.**
- MCP tool spec: `examples/notes-angee/addons/example/notes/mcp_tools.py` (the
  only existing `GraphQLTool` site) — flat scalar projection only.
- `ImplClassField` registry: `agents_integrate_anthropic` (InferenceBackend) and
  `storage.Backend` are the two existing mirrors. Plugin composition template:
  `iam_integrate_oidc` (schema `extend=True`) + `agents_integrate_anthropic`
  (autoconfig registers one impl).
- Derived-from-body GraphQL field: `MarkdownPageType.excerpt` (`schema.py:77`).
- Body write mutation: `update_page_body` (`schema.py:258`).

**Dependency check.** Add **markdown-it-py** (already in `uv.lock` transitively
via `rich`; promote to a direct dep). No other new dependency — diff/preview is
stdlib `difflib`; search v1 is ORM `Q(...icontains)`. `docs/stack.md` gets one
new backend row (the rule: stack row + manifest change together).

**Thin caller check.** MCP `register` declares specs; GraphQL resolvers dispatch
to managers; managers orchestrate `write_body`; the markdown text owner does the
parsing. No business logic in the tool layer.

**Deletion check.** Net new capability (no existing knowledge MCP tools), so
lines increase — but every edit composes the existing `write_body`/CAS/revisions/
backlinks owners rather than re-deriving them, and the compiler change is a
reusable owner-level capability (benefits every future tool). The markdown
parsing lives once (shared by read + write).

**Naming check.** `page` for the record, `section`/`heading_path` for structure,
`outline` for the heading map, `backlinks` (existing), `retrieval` for the
provider seam. Tool names: `read_page`, `search_pages`, `patch_page_section`,
`replace_page_text`, `append_to_page`, `page_backlinks` (snake_case agent
surface, per the compiler's convention).

## Design

### 1. Markdown structure owner (new) — `knowledge/markdown_structure.py`
Pure functions over body text (no Django), the single parser shared by the
`outline` read field and the patch write path:
- `parse_outline(body) -> list[OutlineEntry]` — ordered ATX headings
  `(level, text, slug, line)`. (`OutlineEntry` = a frozen dataclass.)
- `section_range(body, heading_path) -> (start, end)` — tail-match the heading's
  ancestor path; **fail-fast**: `SectionNotFoundError` if none,
  `AmbiguousMatchError` if >1. Range = heading line → next heading of
  same-or-higher level (children included).
- `apply_section_op(body, heading_path, op, content) -> str` — splice the raw
  line buffer for `op ∈ {replace, append, prepend}`. Never re-render.
- `splice_unique(body, old, new) -> str` — exact-string, raises
  `AmbiguousMatchError` on 0/≠1 matches (Claude Code's uniqueness invariant).
- Normalize CRLF on entry; keep one trailing blank before the next heading.
Errors: `StructuredEditError(ValueError)` base, with `SectionNotFoundError` /
`AmbiguousMatchError` subclasses (importable by `schema.py`).

### 2. Manager methods (new) — `MarkdownPageManager`
Thin write-orchestrators (read current body → splice → `write_body(expected_hash=)`,
threading `expected_hash` **unchanged** so the locked CAS in `write_body` stays
authoritative):
`patch_section(page, heading_path, op, content, *, expected_hash=None)`,
`replace_unique(page, old, new, *, expected_hash=None)`,
`append(page, content, *, expected_hash=None)`,
`prepend(page, content, *, expected_hash=None)`.
Each inherits CAS + revision recording + backlink rebuild from `write_body`. No
new model fields → **no migration**.

### 3. GraphQL ops (extend `knowledge/schema.py`)
- READ: add `outline: list[OutlineEntryType]` on `MarkdownPageType`
  (`@strawberry_django.field(only=["body"])`, like `excerpt`). Backlinks already
  on `PageType`. No new query.
- SEARCH: a `KnowledgeQuery.search_pages(vault, query, first=20) -> list[PageType]`
  custom `@strawberry.field` that **delegates to the selected `RetrievalProvider`**
  (default lexical = `Q(title__icontains) | Q(markdown__body__icontains)` +
  `apply_ambient_scope()`); add to the bucket `"query"` list.
- WRITE: `KnowledgeMutation.patch_page_section(page, heading_path, op, content,
  expected_hash=None)` and `replace_page_text(page, old, new, expected_hash=None)`
  `@strawberry.mutation`s returning the existing `PageBodyPayload`, with a
  `SectionOp` `@strawberry.enum`. Map `StaleBodyError → STALE_BODY`,
  `UnsupportedPageKindError → UNSUPPORTED_KIND`, `StructuredEditError →
  STRUCTURED_EDIT` (sub-codes `SECTION_NOT_FOUND`/`AMBIGUOUS_MATCH`).

### 4. Retrieval provider seam (new) — extensibility
- `RetrievalProvider(ImplBase)` ABC in knowledge (`search(vault, query, first,
  *, actor_qs) -> queryset/list[Page]`); default `LexicalRetrievalProvider`
  (`key="lexical"`).
- `ImplClassField(base_class=RetrievalProvider, registry_setting=
  "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES", default="lexical")` on the `Vault` (the
  per-namespace selection point) + a `retrieval` property; seed
  `{"lexical": "...LexicalRetrievalProvider"}` in `knowledge/autoconfig.py`.
- `search_pages` resolves the vault's provider and calls it. A plugin adds
  `"ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES.pgvector": "...PgvectorRetrievalProvider"`
  in its own autoconfig; a `HybridRetrievalProvider` (fan-out + fuse) covers
  lexical+semantic without a list-merge seam.

### 5. MCP tools (new) — `knowledge/mcp_tools.py` + AppConfig `mcp_tools`
`def register(server): register_graphql_tools(server, [...])` declaring:
- `read_page` (query `pages` by sqid → `title, kind, markdown { body body_hash
  word_count outline { level text slug } }, backlinks { page title display_text }`)
- `search_pages` (query → list of `sqid, title, kind`)
- `patch_page_section`, `replace_page_text`, `append_to_page` (mutations →
  `PageBodyPayload` projection `ok, error, error_code, markdown { body_hash }`)
- `page_backlinks` (read-only convenience over `backlinks`)
Add `mcp_tools = "mcp_tools.register"` to `KnowledgeConfig`. The `/mcp` mount
auto-lights via `has_tools()`.

### 6. Compiler change (owner-level) — `addons/angee/mcp/graphql.py`
`read_page`/`page_backlinks` need **nested projection** (`markdown { body
outline { ... } }`, `backlinks { ... }`); the compiler is flat today. Extend it
to support a projection tree of **depth ≤ 2**:
- `GraphQLTool.fields` accepts `str | (name, (child, ...))`.
- Make `leaves` a recursive plan; update `_compile`, `_document` (render `wire {
  children }`), `_output_schema` (nested object/array schemas), `_project`
  (recurse; nullable single objects + per-element lists + child id→sqid),
  `_validate` (recurse, fail-fast on unknown child, reject depth > 2).
- Keep the plan as nested tuples / a pydantic submodel (`_CompiledTool` is a
  fastmcp pydantic `Tool` — no frozen dataclass). Child wire names via
  `_wire_field` (`display_text → displayText`).

### 7. Plugin skeleton (new) — `addons/angee/knowledge_graph_pgvector/`
Proves extensibility end-to-end (no edit to knowledge):
`apps.py` (`depends_on=("angee.knowledge","angee.mcp")`, `schemas`, `mcp_tools`,
maybe `autoconfig`), `autoconfig.py` (registers `"ANGEE_KNOWLEDGE_RETRIEVAL_
CLASSES.pgvector"` — a stub provider for now), `schema.py` (a `related_pages`
field via `@strawberry_django.type(Page, extend=True)`), `mcp_tools.py` (a
`semantic_search` `GraphQLTool` over its own query), `README.md` (the contract).
Ship it **not** in the example `INSTALLED_APPS` (it's the template/contract; a
real pgvector model + migration is out of scope), but with a test that composes
it to prove the seam.

## File-by-file

| File | Change |
|---|---|
| `pyproject.toml` + `uv.lock` | `uv add "markdown-it-py>=4.2"` |
| `docs/stack.md` | one backend row (markdown-it-py: CommonMark tokenizer w/ source line spans) |
| `addons/angee/knowledge/markdown_structure.py` | NEW pure-text owner (§1) |
| `addons/angee/knowledge/models.py` | manager methods (§2) + import the text owner; errors |
| `addons/angee/knowledge/retrieval.py` | NEW `RetrievalProvider`/`LexicalRetrievalProvider`/`HybridRetrievalProvider` (§4) |
| `addons/angee/knowledge/models.py` (Vault) | `retrieval_class` ImplClassField + `retrieval` property |
| `addons/angee/knowledge/autoconfig.py` | seed `ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES` (NEW file if absent) |
| `addons/angee/knowledge/schema.py` | `OutlineEntryType`, `MarkdownPageType.outline`, `KnowledgeQuery.search_pages`, `SectionOp`, `patch_page_section`/`replace_page_text` mutations, bucket wiring |
| `addons/angee/knowledge/mcp_tools.py` | NEW (§5) |
| `addons/angee/knowledge/apps.py` | `mcp_tools = "mcp_tools.register"` |
| `addons/angee/mcp/graphql.py` | nested-projection compiler (§6) |
| `addons/angee/knowledge_graph_pgvector/*` | NEW plugin skeleton (§7) |
| `tests/test_markdown_structure.py` | NEW — outline/section/splice + fail-fast |
| `tests/test_mcp_graphql.py` | NEW — nested projection compile + project |
| `tests/test_knowledge.py` / `test_knowledge_graphql.py` | extend — manager CAS/section, search, patch mutations |
| `tests/test_knowledge_mcp.py` | NEW — every `GraphQLTool` spec `_compile`s; pgvector plugin composes |

## Test & verify (from repo root)
```
uv add "markdown-it-py>=4.2"
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py makemigrations base knowledge notes   # expect: no changes
uv run examples/notes-angee/manage.py schema && uv run examples/notes-angee/manage.py schema --check
uv run examples/notes-angee/manage.py shell -c "from angee.mcp.server import mcp_server; print(sorted(t.name for t in mcp_server()._tool_manager._all_tools().values()))"
uv run pytest tests/test_markdown_structure.py tests/test_mcp_graphql.py tests/test_knowledge.py tests/test_knowledge_graphql.py tests/test_knowledge_mcp.py
```

## Risks / gotchas (from the spike)
- Section range includes child sections; "append to a heading" lands after
  children. If prose-only append is wanted, add a `before_children` boundary —
  decide at the tool contract (default: section-inclusive, documented).
- Thread `expected_hash` unchanged into `write_body` (re-hashing the manager's
  own read would defeat CAS).
- Revisions only record inside a request (RevisionMiddleware) — true for the
  mutations; note it for any script path.
- Blank-line/CRLF hygiene on splice; slug dedupe is local (fine — we key on
  heading path, not anchor).
- Body content search is a seq scan (no index); a GIN/FTS index is a migration
  and is the pgvector/FTS plugin's concern, not v1.
- `mcp_tools.py` is named `mcp_tools` (never `mcp`) to avoid shadowing the
  third-party `mcp` package.

## Out of scope (later, via plugin)
Embeddings, vector/graph retrieval, FTS indexes, the real pgvector model +
migration. The seams (§4 provider registry, §6 nested projection, §3 search
delegation) are what make those drop-in.
