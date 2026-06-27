# Prior art: how AI agents read & update markdown knowledge (notes/pages)

Synthesized from a verified multi-source research run (30 sources, 114 extracted
claims, 75 adversarial verdicts — 3 refuted, the rest high-confidence).
Scope: **reading + editing markdown now**; semantic retrieval / graph RAG is out
of scope (a later phase). Goal: a minimal tool surface for the Angee knowledge
addon (markdown notes/pages as Django + React + GraphQL records, page tree,
wikilinks, agents over ACP/MCP).

## Executive summary

Across coding agents, MCP servers, and reverse-engineered tool schemas the field
has converged on a small, repeated design: **line/heading-addressable reads, a
partial-edit primitive guarded by a compare-and-set precondition, and whole-file
write kept as the escape hatch.** The dominant edit granularity for general code
is **exact-string replace** (Claude Code `Edit`, Q CLI/Gemini CLI `str_replace`,
official filesystem MCP `edit_file`); the dominant granularity for **markdown
specifically** is the **heading/block/frontmatter-anchored patch** plus
append/prepend (cyanheads obsidian-mcp-server, mcp-obsidian, Basic Memory). The
recurring reliability mechanism is **read-before-edit + exact match + uniqueness**
acting as a check-and-set against stale or hallucinated content. The recurring
efficiency mechanism is **never put the whole file in the token window**: read
outlines/sections, edit by anchor, return only diffs. A strong secondary lesson:
**fewer, intent-shaped tools beat a sprawling CRUD surface** (obsidian-semantic-mcp
collapses 20+ tools into 5). For Angee the right shape is GraphQL-mutation-native
tools that mirror these patterns, with version-token CAS standing in for
"read-before-edit," and the page tree + wikilinks exposed as first-class
navigation (something filesystem MCPs lack).

---

## Lens 1 — Agent frameworks & products

### Claude Code's built-in file tools (the reference design)
- **Read** returns `cat -n` line-numbered content (1-indexed), absolute path
  required, ~2000-line default with `offset`/`limit` paging; oversized whole-file
  reads return a "PARTIAL view" page telling the agent how to read more, and an
  explicit over-limit `offset/limit` read errors. Line numbers exist so a
  follow-up `Edit` can anchor. (code.claude.com/docs/en/tools-reference;
  deepwiki.com/weimeng23/claude-code-src; israynotarray.com)
- **Edit** = exact-string replace (no regex/fuzzy). Three checks: **read-before-edit**
  (file read in this conversation and unchanged on disk), **exact match**
  (whitespace/indent/line-endings included), **uniqueness** (`old_string` must
  match once, else add context or `replace_all:true`). `old_string` should be
  minimal (1–3 lines) — excess context is "an error" / token waste. Internally a
  diff-style patch (`getPatchForEdit`) with mtime staleness checks. (tools-reference;
  repovive.com; gist wong2; bgauryy gist)
- **Write** = whole-file overwrite (no append/merge); refuses to overwrite a file
  not Read first; guidance routes partial changes to Edit "because it only sends
  the diff" — an explicit token-efficiency rationale.
- **MultiEdit** applies multiple edits to one file **atomically and sequentially**
  (each on the prior result); any failure aborts all.
- **NotebookEdit** edits Jupyter cells by **`cell_id`** (replace/insert/delete) —
  i.e. Claude Code uses *structured anchors* for notebooks instead of exact-string,
  a useful precedent for structured (heading-anchored) markdown edits.
- **Grep** (ripgrep) with `content`/`files_with_matches`/`count` modes +
  `head_limit`/`offset` paging, auto-excludes `.git`; **Glob** matches filenames,
  sorts by mtime, caps at 100 with a truncation flag. Both exist for token-efficient
  discovery "without reading every file into context"; the prompt says ALWAYS use
  them rather than shelling out to `grep`/`rg`.

### Other coding agents (edit-format diversity)
- **Aider** ships multiple formats by granularity: `whole` (full overwrite),
  `diff`/`diff-fenced` (git-conflict-marker SEARCH/REPLACE blocks), `udiff`
  (line-number-less unified diff interpreted as search/replace). It **auto-selects
  the best format per model**. `whole` is "slow and costly" (returns the entire
  file); diffs return only changes. (aider.chat/docs/more/edit-formats)
- **Aider's udiff benchmark** is the canonical edit-reliability datapoint: moving
  GPT-4 Turbo from SEARCH/REPLACE to udiff raised a refactoring score **20% → 61%**
  and cut "lazy" partial edits ~3×, because udiff cues the model to emit
  machine-readable data and anchors by surrounding context, not line numbers.
  Disabling flexible patch application caused a **9× increase in edit errors**.
  (aider.chat/docs/unified-diffs)
- **Gemini CLI** `smart-edit` uses **three-tier progressive matching**: exact →
  whitespace-insensitive → whitespace-flexible regex, then LLM self-correction of
  the params. **Q CLI** `str_replace` errors if `old_str` matches zero or multiple
  times; `fs_write` has create/str_replace/insert/append modes; `fs_read` does
  `start_line`/`end_line` ranges. **Codex CLI** `apply_patch` uses a context-anchored
  V4A diff (match by surrounding code, not line numbers). (sumitgouthaman.com;
  codex.danielvaughan.com)
- **Morph** "fast-apply": a specialized model takes terse intent + reconciles
  against current file state, claiming **98% vs ~84%** for plain search/replace, at
  external API cost — the "spend tokens elsewhere to buy edit accuracy" option.
  (morphllm.com/edit-formats)

### AI note apps (markdown CRUD products)
- **Khoj Obsidian plugin (PR #1109)** is the most on-point note-app editor:
  refactored to a **SEARCH/REPLACE format "also used by aider"**, normalizes text
  before find/replace, and makes edits safe by **previewing (yellow highlight) with
  explicit Accept/Cancel** before applying. (github.com/khoj-ai/khoj/pull/1109)
- **Claudian** and **Claude Code IDE** (Obsidian plugins) show the **"vault as the
  agent's working directory"** pattern: the agent reuses the coding agent's native
  Read/Write/Edit/Grep tools on `.md` files rather than bespoke note verbs; Claudian
  adds inline word-level **diff preview before apply** and `@`-mention of files/MCP
  servers. Claude Code IDE delegates editing to Claude Code's filesystem tools and
  only contributes editor context (open file/selection). (github.com/YishenTu/claudian;
  community.obsidian.md/plugins/claude-code-ide)
- **Obsidian Copilot** — Agent Mode auto-triggers vault search/edit tools, one-click
  "Edit and Apply"; but **edit granularity is unspecified and the edit backend is
  closed-source**, so weak as a mechanics reference. (github.com/logancyang/obsidian-copilot)
- **Reor / Khoj (core)** are retrieval/embedding-centric (vector auto-linking, RAG
  Q&A) — **out of scope** here; note Reor's repo was **archived March 2026**.

---

## Lens 2 — Open-source MCP servers for notes/markdown

| Server | Edit granularity | Read/nav | Safety |
|---|---|---|---|
| **Official Filesystem MCP** | `write_file` (whole, "exercise caution"), `edit_file` (array of `{oldText,newText}` exact-string, multi-line, indent-preserving) | `read_text_file` (+`head`/`tail`), `read_multiple_files`, `directory_tree`, `search_files` (glob), `list_directory`, `get_file_info` | **`dryRun`** returns git-style diff before applying; tools annotated read-only vs write + idempotency; sandboxed to allowed dirs/Roots (github.com/modelcontextprotocol/servers/src/filesystem) |
| **cyanheads/obsidian-mcp-server** (richest markdown surface) | `obsidian_write_note` (whole **or** section PATCH; refuses clobber unless `overwrite:true`), **`obsidian_patch_note`** (append/prepend/replace at **heading / block-ref / frontmatter**), `obsidian_append_to_note` (upsert), `obsidian_replace_in_note` (literal/regex), `obsidian_manage_frontmatter`/`_tags` (atomic key ops) | `obsidian_get_note` in **4 projections**: raw, full (content+frontmatter+links), **document-map (heading/block/frontmatter outline)**, section-by-name; search substring/JSONLogic/BM25 + cursor paging + per-file clipping | clobber-refusal, path-scope policy, delete confirm, **`previousSizeInBytes`/`currentSizeInBytes`** drift telemetry |
| **MarkusPfundstein/mcp-obsidian** (lean baseline) | `patch_content` (insert at heading/block/frontmatter), `append_content` (append-only), `delete_file` | `list_files_in_vault`/`_in_dir`, `get_file_contents`, `search` (simple full-text) | via Obsidian Local REST API (API key/host/port) |
| **obsidian-semantic-mcp** (tool-count lesson) | one **`edit`** op with 5 modes: `window` (fuzzy), `append`, `patch` (heading paths like `"H1::Sub"`), `at_line`, `from_buffer` | intelligent **fragment retrieval** (returns relevant fragments w/ lineStart/lineEnd) to cut tokens | **20+ tools collapsed into 5** semantic ops w/ workflow hints; content buffer to recover failed edits (github.com/aaronsb/obsidian-semantic-mcp) |
| **Basic Memory** (closest architectural analog) | **`edit_note`** with 6 modes: append, prepend, find_replace, **replace_section, insert_before_section, insert_after_section**; `write_note` requires **`overwrite:true`** | `read_note` (title/permalink/`memory://` URL, paging), `view_note`, `read_content` (raw), `list_directory` (depth+glob), `search_notes` | **`expected_replacements`** (default 1) validates match count before applying; overwrite-protection (docs.basicmemory.com) |
| **Official Memory (knowledge-graph) MCP** | entity/relation/observation triples; `add/delete_observations` etc. | `read_graph`, `open_nodes`, `search_nodes` | structured-triple updates — **graph-RAG-adjacent, mostly out of scope** but the contrast model for Angee's later graph layer |

---

## Lens 3 — Leaked / reverse-engineered tool schemas

- **Piebald-AI/claude-code-system-prompts** and **wong2 gist** are the primary
  artifacts: versioned dumps of Claude Code's system prompt + all 27 built-in tool
  descriptions (Read/Write/Edit/MultiEdit/Grep/Glob/NotebookEdit). The verbatim
  Edit invariant: *"You must Read the file in this conversation before editing, or
  the call will fail. `old_string` must match the file exactly, including
  indentation, and be unique — the edit fails otherwise. Strip the Read line prefix
  (line number + tab) before matching. `replace_all: true` replaces every
  occurrence."* Write: *"Prefer the Edit tool… it only sends the diff. Only use this
  tool to create new files or for complete rewrites."*
- **asgeirtj/system_prompts_leaks** is the multi-vendor corpus (Claude Code, Cursor,
  Codex, Copilot, Gemini, Grok, Notion, …) for cross-tool comparison.
- The invariants reproduce across primary/secondary/blog sources: **read-before-edit
  as a staleness CAS**, **exact-string uniqueness**, **line-numbered reads as edit
  anchors**, **whole-file Write split from partial Edit for token reasons**, and
  **structured anchors (cell_id) for notebooks**.

---

## Cross-cutting: edit-granularity taxonomy & safe-edit patterns

**Granularity, cheapest/safest → most expensive/most stable:**
1. **Append/prepend** — append-only, no existing text touched. Safest mutation
   (mcp-obsidian `append_content`, Basic Memory `append`).
2. **Section/heading/block-anchored patch** — the **markdown-native** primitive;
   target by heading path, block ref, or frontmatter (cyanheads, mcp-obsidian,
   Basic Memory `replace_section`/`insert_*_section`, semantic `patch`). Survives
   reflow; no line-number fragility.
3. **Exact-string replace** — general default (Claude Code, Q/Gemini CLI, filesystem
   MCP); precise, token-light, but brittle to whitespace and ambiguous matches.
4. **Search/replace + unified diff** — Aider `diff`/`udiff`, Codex V4A; context-anchored,
   no line numbers; most reliable *for code* per Aider's benchmark.
5. **Line-addressed + checksum (CAS tag)** — antirez's proposal: READ/SEARCH return
   `line# + 4-char checksum tag (~2.5 tokens)`; edits cite `path:line:tag`, so a
   stale/hallucinated line is rejected without re-emitting old text. Whole-file CRC32
   is cheaper but over-rejects unrelated changes. (antirez.com/news/166)
6. **Whole-file overwrite** — simplest and most stable, **most token-expensive**;
   keep as escape hatch, guard with clobber/overwrite protection.
7. **Fast-apply semantic model** — highest accuracy, external cost (Morph).

**Recurring safety mechanisms:** read-before-edit (mtime/version CAS) · exact match
+ uniqueness · `expected_replacements`/replace-count validation · clobber protection
(`overwrite:true`) · **dryRun/diff preview + human Accept/Cancel** · byte-size/mtime
drift telemetry · progressive/flexible matching fallback (Gemini 3-tier; Aider's
flexible patching prevents a 9× error spike).

**Recurring efficiency mechanisms:** line-numbered/`cat -n` reads · `offset/limit`
+ `head/tail` paging · **outline/document-map projection** to find targets without
fetching the body · section-by-name reads · fragment retrieval · Grep/Glob discovery
instead of reading files · return only diffs, never the whole file.

**Tool-count:** obsidian-semantic-mcp's thesis — 20+ granular tools overwhelm
agents; **~5 intent-shaped operations with workflow hints** perform better.

---

## Recommendation — minimal tool surface for the Angee knowledge addon

Angee notes/pages are **markdown records over GraphQL** with a **parent/child page
tree** and **wikilinks**, edited by agents over ACP/MCP. So the "file" is a DB
record and "read-before-edit" becomes a **version-token compare-and-set** on the
record (Angee's GraphQL already owns `updated_at`/version). Mirror the converged
patterns, keep it to ~6–7 intent-shaped tools, and make the tree + wikilinks
first-class (the thing filesystem MCPs can't do).

1. **`page_read`** — by id / path / wikilink-title, with **projections**: `outline`
   (heading + anchor map, default for navigation), `section` (by heading), or `full`
   body. Returns a **version token** for CAS. *(cat -n reads + cyanheads document-map.)*
2. **`page_search`** — substring/regex content search, filterable by subtree/tags;
   returns **clipped, paginated** matches with page ids (token-budgeted). *(Grep.)*
3. **`page_tree`** — list children / walk the parent-child tree (Angee's native
   "glob"/`directory_tree`).
4. **`page_edit`** — **primary mutation**: heading/anchor-addressed
   `append`/`prepend`/`replace_section`, **plus** an exact-string `replace`
   (unique-or-`replace_all`) for fine edits; guarded by the **version token (reject
   on stale)** and an `expected_replacements` count. *(cyanheads/Basic Memory +
   Claude Code Edit.)*
5. **`page_append`** — append-only fast path (cheapest/safest; the common "add a
   note" case).
6. **`page_write`** — whole-page create/overwrite, **clobber-protected**
   (`overwrite:true` / version required); only for new pages or full rewrites.
7. **`page_link` / `page_move`** — manage wikilinks and tree position; **auto-update
   backlinks on rename** (the Obsidian-CLI affordance). Angee-native value-add.

**Bake in:** `dryRun` returning a diff for the React human-in-the-loop confirm flow
· outline-first navigation so agents patch surgically without fetching whole pages ·
heading/anchor addressing as primary (not raw line numbers — markdown reflows) ·
version-CAS on every mutation · append-only as the cheap default.

**Defer (graph-RAG phase):** embeddings/semantic search, entity/relation triples
(the Memory-MCP model) layered *over* this markdown CRUD core — don't let it leak
into the edit surface now.

---

## Caveats
- 3 of 75 claims were refuted; several Claude Code internals lean on blog/secondary
  sources, but are corroborated by the primary leaked schema and the live tool
  definitions.
- Aider's 20%→61% and Morph's 98% are **model-specific / vendor-claimed**; treat as
  directional, not guarantees.
- Khoj/Reor are retrieval-centric (out of scope); Reor is archived.

## Open questions
- GraphQL-mutation-native edits **vs** export-to-`.md` + a filesystem MCP? (Lean
  native + version-CAS.)
- Heading-anchored patch **vs** antirez line+checksum CAS for the precise mode —
  which fits markdown records best?
- Wikilink semantics on rename/move (auto-update backlinks) — owned where (model vs
  tool)?
- Add a fast-apply model later, or is heading-anchored patch + CAS enough?

## Sources
Claude Code: code.claude.com/docs/en/tools-reference · deepwiki.com/weimeng23/claude-code-src ·
israynotarray.com (built-in tools explained) · repovive.com (Edit tool) ·
github.com/Piebald-AI/claude-code-system-prompts · gist wong2 · bgauryy gist ·
github.com/asgeirtj/system_prompts_leaks.
Edit formats: aider.chat/docs/more/edit-formats · aider.chat/docs/unified-diffs ·
antirez.com/news/166 · sumitgouthaman.com/posts/file-editing-for-llms ·
morphllm.com/edit-formats · dev.to (5 editing strategies) · codex.danielvaughan.com (V4A).
MCP servers: modelcontextprotocol/servers (filesystem, memory) ·
github.com/cyanheads/obsidian-mcp-server · github.com/MarkusPfundstein/mcp-obsidian ·
github.com/aaronsb/obsidian-semantic-mcp · docs.basicmemory.com.
Note apps: github.com/khoj-ai/khoj (+PR #1109) · github.com/YishenTu/claudian ·
github.com/logancyang/obsidian-copilot · community.obsidian.md/plugins/claude-code-ide ·
github.com/reorproject/reor.
