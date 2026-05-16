# Entity Extraction Architecture

Entity extraction is an independent system for typed mention extraction and canonical linking.

It is shared infrastructure. AQL, Attention, Agents, UI, and knowledge-graph workflows should use it instead of each owning separate NER/entity-linking logic.

## Docs

- `ENTITY_EXTRACTION_2026-04-25.md`: implementation note for the first entity extraction/linking layer
