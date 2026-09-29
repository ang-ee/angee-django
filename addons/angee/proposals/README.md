# Proposals

- The [project setup contributor](models.py) composes `Round.objects.provision` after milestones and bindings; `RoundTemplate` owns dates and topic keys, and admission owns responders and tracks.
- The [question projections](schema.py) count readable unanswered recipients through the same predicate as `clarification_waiting`. Inherited question visibility reads through the parent task's current ReBAC policy.
- [`Round.objects.resolve_clarification`](models.py) owns single and batch resolution. Each selected question needs management authority, an observed revision and an eligible state; batches use [work's partial-success contract](../work/README.md).
- The [record contributions](web/src/record-rounds.tsx) place ceremonies, People and Approach on Round and Project records. The [active-round selector](models.py) picks the newest readable collecting or opened round from the project, source Task or proposal track; historical rounds remain in the Rounds tab.
- [Answer declarations](web/src/index.tsx) bind the [shared visibility widget](../../../packages/ui/src/widgets/visibility.tsx) to server choices and the revision-checked verb. The [comparison body](web/src/comparison-body.tsx) shows one audience summary per column.
