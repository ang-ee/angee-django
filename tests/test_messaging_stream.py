"""Server facts consumed by the record thread stream."""

from datetime import datetime

from rebac import actor_context, system_context

from tests import test_messaging as messaging_models
from tests.conftest import execute_schema, result_data
from tests.test_messaging_graphql import _platform_admin, _request, _schema


def test_record_stream_projects_latest_edit_and_unpaged_reply_count(composed_tables):
    del composed_tables
    reader = _platform_admin("stream-reader")
    with system_context(reason="test.messaging.stream.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Conversation")
    with actor_context(reader):
        first = ticket.message_post("First entry")
        reply = ticket.message_post("First reply", parent=first)
        ticket.message_update_content(reply, body="Edited reply")

    payload = result_data(
        execute_schema(
            _schema(),
            """
        query Stream($id: ID!) {
          record_thread(input: {model_label: "messaging.ThreadedTicket", record_id: $id, message_limit: 1}) {
            message_result_count reply_count audience_label post_kinds
            messages { id edited_at is_self is_reply author_label }
          }
        }
        """,
            {"id": ticket.sqid},
            request=_request(reader),
        )
    )["record_thread"]

    assert payload["message_result_count"] > len(payload["messages"])
    assert payload["reply_count"] == 1
    assert payload["post_kinds"] == ["COMMENT", "NOTE"]
    assert payload["audience_label"] is None
    assert payload["messages"][0]["id"] == reply.sqid
    assert datetime.fromisoformat(payload["messages"][0]["edited_at"])
    assert payload["messages"][0]["is_self"] is True
    assert payload["messages"][0]["is_reply"] is True
    assert payload["messages"][0]["author_label"]

    def filtered_reply_count(search: str, kinds: str) -> int:
        return result_data(
            execute_schema(
                _schema(),
                """
                query StreamCount($id: ID!, $search: String!, $kinds: [String!]!) {
                  record_thread(input: {model_label: "messaging.ThreadedTicket", record_id: $id,
                    message_limit: 1, search: $search, message_types: $kinds}) {
                    reply_count
                  }
                }
                """,
                {"id": ticket.sqid, "search": search, "kinds": [kinds]},
                request=_request(reader),
            )
        )["record_thread"]["reply_count"]

    assert filtered_reply_count("Edited", "comment") == 1
    assert filtered_reply_count("First entry", "comment") == 0
    assert filtered_reply_count("Edited", "notification") == 0

    other_role = result_data(
        execute_schema(
            _schema(),
            """
            query StreamRole($id: ID!) {
              record_thread(input: {model_label: "messaging.ThreadedTicket", record_id: $id, role: "source"}) {
                reply_count message_result_count
              }
            }
            """,
            {"id": ticket.sqid},
            request=_request(reader),
        )
    )["record_thread"]
    assert other_role == {"reply_count": 0, "message_result_count": 0}
