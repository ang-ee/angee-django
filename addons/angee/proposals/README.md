# Proposals

The project `apply_setup` contributor consumes round choices and composes
[`Round.objects.provision`](models.py) after milestones and bindings exist.
`RoundTemplate` remains the owner of dates, topic keys and milestone references;
admission remains the owner of responders and tracks. Optional addons contribute
round setup values through `Round.setup_values`.

Project and task fields `questions_waiting_for_me` and `questions_passed_on`
count readable open questions with unanswered recipients. Passed counts require
management of the round and count each question once, including when several
responders owe answers. Both projections reuse the same recipient predicate as
`clarification_waiting`, with SQL annotations instead of per-row queries.
