import { graphql } from "@angee/gql/console";

export const ExtractionRecordEvidenceDocument = graphql(`
  query ExtractionRecordEvidence($id: ID!) {
    extraction_evidence(id: $id) {
      extraction {
        id revision status error_code schema_id schema_digest profile created_at
      }
      result
      schema
      provenance
      documents { identity selector lines { identity selector } }
      retired { identity kind reason }
      sources {
        id position content_hash file message_part source_message
      }
      pages { position source_page result }
      parts {
        position source_page mime_type kind method content_hash
        width height dpi value claims metadata duration_ms
      }
    }
  }
`);
