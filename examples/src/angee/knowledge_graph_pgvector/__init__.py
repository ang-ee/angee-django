"""Semantic-retrieval plugin skeleton for the knowledge addon.

Proves that a graph-RAG / pgvector capability bolts onto the knowledge base
addon through its declared seams alone — a retrieval-backend key, a GraphQL
projection/query, and an MCP tool — with **zero edits to ``angee.knowledge``**.
The bundled backend is a lexical stub; a real plugin replaces it with an
embedding column + ANN query (its own model and migration). See ``README.md``.
"""
