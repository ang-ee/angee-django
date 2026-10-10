# Workflow conversations

`start_conversation` is a DATABASE step over ACP's persisted session API. Config
selects an agent by public id and a plain `prompt_template`. The engine binds
JSON into input `data`; `str.format_map` exposes only `subject` (`type`, `sqid`)
and that JSON as `input`. For example: `Answer {subject[sqid]} in
{input[language]}.` No model instances or workflow rows enter the template.
The agent runtime renders subject content through `render_view_context` under
the turn poster's REBAC scope.

Input may name a `session` to continue; omission opens a session owned by the
run actor. Continuations must have the same agent and subject context. Output
names the exact `session` and completed `turn`, with `text` from `AgentTurn.text`.
An empty or whitespace answer selects `no_reply`; other answers select `done`.

The session owner queues `agents.run_session` after commit. The workflow watches
that turn and retains its ids in the checkpoint, so waking does not post again.
Only status changes wake the step; transcript flushes do not. A watch-only
registration observes the turn without adding an admission trigger source.
Config `timeout` defaults to the worker task time budget plus two minutes for
queueing. A pending turn is re-enqueued at most twice, with a fresh deadline for
each delivery; the session owner claims duplicate deliveries safely. Exhaustion
or a running turn past its deadline cancels the turn as the run actor and fails
readably. Provider failure and Stop also fail. Operator Retry posts another turn
on the retained session.

Grant the workflow principal the agent's `caller` relation through
`Agent.grant_record_access`. This allows opening and posting sessions without
agent-definition read or editing. Every execution also checks that the version's
publisher still holds `call` on the selected agent. Tool execution uses the
agent's distinct service user; its tool grants and record access remain required.
For comment responding, the workflow holds channel `replier` authority and the
agent holds only read access and read tools. Scheduling belongs to the
`workflows_posts` step that consumes the completed answer.

Sessions retain replay history for explicit continuation. Route completed
conversation output through `close_conversation` when finished; it invokes
`AgentSession.close` and forwards the completed answer. Route `no_reply` through
the same close step to release an empty conversation. Workflow
cancellation removes its watches; Stop/Close controls the queued ACP turn.

Native flow and recovery cases are in `tests/native_workflows_posts.py`, collected
through `tests/test_workflows_posts.py` on SQLite and PostgreSQL.
