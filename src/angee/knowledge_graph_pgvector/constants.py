"""Wire names and bounded work budgets for the passage index."""

EMBED_PAGE_TASK = "knowledge_graph_pgvector.embed_page"
EXTRACT_PASSAGE_TASK = "knowledge_graph_pgvector.extract_passage"
RECONCILE_TASK = "knowledge_graph_pgvector.reconcile_index"
RECONCILE_SECONDS = 60
RECONCILE_BATCH = 100

EMBEDDING_ROLE = "knowledge_embedding"
EMBED_BATCH = 32
PASSAGE_BYTES = 2000
PASSAGE_OVERLAP = 100
TRIGRAM_LEAD = 0.05
CANDIDATE_MULTIPLIER = 4
QUERY_CACHE_SECONDS = 300
TRIGRAM_CANDIDATES = 3
MIN_CHUNK_BYTES = 4
CHUNK_FILL_DIVISOR = 2
CHUNK_OVERLAP_DIVISOR = 8
