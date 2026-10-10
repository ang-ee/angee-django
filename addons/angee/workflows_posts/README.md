# Comment reply workflows

This satellite composes a thin responding workflow. Posts owns comment
eligibility and reply preparation; agents owns persisted conversations;
workflows owns admission, execution, evidence and retry. Neither posts nor
agents depends on this satellite.

`check_replied` is a DATABASE step over the inbound `messaging.Message` subject.
It locks the subject and calls `MessagePublic.reply_state()`. A true result
selects `replied` and ends the branch; false selects `open`. The posts predicate
recognizes active outbound replies, replies ingested from the channel's own
handle, and comments authored by that handle. The step records the comment and
notes why responding ends or proceeds.

`start_conversation` returns the completed turn's `session`, `turn` and final
answer `text`. Empty or whitespace text selects `no_reply`. `schedule_reply`
is a DATABASE step accepting that output. It verifies the completed turn was
created by this run for this subject, then calls
`MessagePublic.reply_to_comment(body=text, actor=ctx.actor, local=..., creation_key=...)`.
The creation key is `workflow-reply:<run sqid>` and remains stable across retries
and task redelivery. Message owns replay, permissions, scheduling and delivery;
the step records the reply as created and returns its actual `message_id`,
`status` and `scheduled_at`. Local `agent_turn` metadata names the completed turn
and is protected from ordinary metadata writes. Notes distinguish operator
approval, timed release and immediate delivery.

An answer arriving during inference raises posts' typed `CommentAnswered` on
insertion. The step settles as `replied` with empty output and closes the session.
Creation-key replay runs first, so retrying an existing reply still returns its
original `scheduled` output. Route `replied` to the same end as `check_replied`.

Install a workflow resource document with a concrete public agent id:

```yaml
_meta:
  model: workflows.Workflow
rows:
  - xref: comment_reply
    fields:
      key: comment_reply
      name: Comment reply
      subject_model: messaging.Message
      publish: true
      draft:
        nodes:
          check:
            step: check_replied
            next: {open: converse}
          converse:
            step: start_conversation
            input: {from: input, project: true}
            config:
              agent: example.reply_agent
              prompt_template: >-
                Read comment {subject[sqid]} and its thread, consult knowledge
                where useful, and return the answer text. Return empty text
                when no reply is needed.
            next: {done: schedule, no_reply: close}
          schedule:
            step: schedule_reply
            input: {from: converse}
            next: {scheduled: close, replied: close}
          close:
            step: close_conversation
            input: {from: converse}
        results:
          - {from: check, when: [replied]}
          - {from: schedule, when: [scheduled]}
          - {from: schedule, when: [replied]}
          - {from: converse, when: [no_reply]}
```

The result bindings preserve `replied`, `scheduled` or `no_reply` while
`close_conversation` closes sessions after answered and empty turns through
`AgentSession.close`. The consumer supplies its agent, workflow, feed trigger
and grants through resource documents. Install `workflows_messaging` for
`message_ingested`, scoped to feed channels with condition
`{direction: {_eq: inbound}, is_original_post: {_eq: false}}`. Historical and
trashed messages do not enter that source. Trigger admission owns its request
key; the scheduling creation key protects one run's effect.

Grant the workflow principal `caller` on the agent and `replier` on the channel,
with channel read access for the subject and reply evidence. The workflow
version publisher must retain agent `call`. Grant the agent service user only
channel/handle reads and knowledge-vault `viewer` access. Its responding toolset
contains `read_comment_thread`, `read_message_text`, `search_pages` and
`list_vaults`; the agent returns text without write or reply authority.

Posts applies `Feed.reply_hold`: null holds an approval-only draft with no
scheduled release; a positive number schedules a draft that many hours out;
zero queues delivery immediately. Console approval and the release sweep remain
messaging concerns.

The FastMCP registry and `agents.grants.sync_builtin_tool_catalogue` own tool
registration and catalogue synchronization. Sync creates or adopts the builtin
server and runs during in-process agent provisioning and before named resource selections
resolve, without demo resources. Catalogue identities are `agents.mcp_angee`
and `agents.tool_<name>`. Named tool bundles use `agents/toolrole` and
`agents/tool_grant`; runtime advertisement and per-call revocation checks stay
in `agents_runtime_pydantic.toolsets`.

Relation-marked step configuration fields accept resource references such as
`example.reply_agent`; publishing resolves them through the resources ledger.
Set the responding agent's persistent `resource_reader: false` to opt out of
the default generated reader bundle. Agent writes revoke that membership in the
same transaction; enabling it grants membership only to a provisioned in-process
agent. Catalogue synchronization updates tools and bundle contents.

Native contracts live in `tests/native_workflows_posts.py`.
`tests/test_workflows_posts.py` composes the messaging trigger, agent runtime,
knowledge tools and posts reply owner. Its PostgreSQL selection covers
scheduling races, creation-key replay and retry rollback.
