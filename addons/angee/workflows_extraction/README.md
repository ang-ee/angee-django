# Extraction workflow adapter

This addon owns the `document_extraction` workflow resource and its four steps.
The independent `angee.extraction` addon owns evidence, profiles, acquisition,
inference, schema, and permissions. Steps pass typed carrier references to that
domain and retain its result.
