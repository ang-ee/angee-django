# Proposals

The project `apply_setup` contributor consumes round choices and composes
[`Round.objects.provision`](models.py) after milestones and bindings exist.
`RoundTemplate` remains the owner of dates, topic keys and milestone references;
admission remains the owner of responders and tracks. Optional addons contribute
typed round setup fields through `input_extensions`, consumed by `Round.setup_values`.

Project and task fields `questions_waiting_for_me` and `questions_passed_on`
count readable open questions with unanswered recipients. Passed counts require
management of the round and count each question once, including when several
responders owe answers. Both projections reuse the same recipient predicate as
`clarification_waiting`, with SQL annotations instead of per-row queries.

A question widened to inherited visibility reads through its parent task under
that parent's current ReBAC read policy. Restricted questions do not take this
path. Recipients retain read/comment access only.

`resolve_proposal_clarification` and `resolve_proposal_clarifications` use the
round manager's `resolve_clarification` verb. Each selected question requires
management authority, its observed revision and an eligible state. Batches use
the [same partial-success contract as work](../work/README.md): refused rows are
reported without rolling back eligible rows.
