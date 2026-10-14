"""Comments pages one interleaved window over messages and native changesets."""

from rebac import actor_context

from angee.base.changesets import changeset
from tests.conftest import execute_schema, result_data
from tests.messaging_campaign import comment_record as comment_record
from tests.test_messaging_graphql import _schema

TIMELINE = """
query Timeline($id: ID!, $limit: Int!, $offset: Int!, $search: String!) {
  record_thread(input: {model_label: "chatterdemo.ChatterDoc", record_id: $id,
    include_history: true, message_limit: $limit, timeline_offset: $offset, search: $search}) {
    error_code timeline_count message_result_count
    timeline {
      __typename
      ... on RecordMessageType { id preview }
      ... on HistorySummaryType { id actor change_reason }
    }
  }
}
"""


def test_comments_pages_one_combined_chronology_and_keeps_record_gate(comment_record):
    case = comment_record
    schema = _schema()
    with actor_context(case.writer):
        with changeset(fresh=True, change_reason="Post first comment"):
            first = case.record.message_post("Window comment one")
        with changeset(fresh=True, change_reason="Window rename one"):
            case.record.title = "First rename"
            case.record.save(update_fields=("title",))
        with changeset(fresh=True, change_reason="Post second comment"):
            second = case.record.message_post("Window comment two")
        with changeset(fresh=True, change_reason="Window rename two"):
            case.record.title = "Second rename"
            case.record.save(update_fields=("title",))

    def read(user, *, limit=50, offset=0, search="Window"):
        return result_data(execute_schema(
            schema, TIMELINE,
            {"id": case.record.sqid, "limit": limit, "offset": offset, "search": search},
            user=user,
        ))["record_thread"]

    whole = read(case.peer)
    assert whole["timeline_count"] == 4
    assert whole["message_result_count"] == 2
    entries = whole["timeline"]
    assert [entry["__typename"] for entry in entries] == [
        "RecordMessageType", "HistorySummaryType", "RecordMessageType", "HistorySummaryType",
    ]
    assert [entry["id"] for entry in entries if entry["__typename"] == "RecordMessageType"] == [
        first.sqid, second.sqid,
    ]
    assert read(case.peer, limit=2)["timeline"] == entries[-2:]
    assert read(case.peer, limit=2, offset=2)["timeline"] == entries[:2]
    assert read(case.peer, limit=2, offset=4)["timeline"] == []
    assert read(case.peer, search="rename")["message_result_count"] == 0
    assert read(case.peer, search="rename")["timeline_count"] == 2
    denied = read(case.outsider)
    assert denied["error_code"] == "NOT_FOUND"
    assert denied["timeline"] == []
