# Workflows on records: design

Design, 2026-10-04. Not implemented yet. Companion: [decisions design](../../decisions/docs/design.md).

## Ontology

What exists and how it relates, independent of any code.

- **Workflow:** a plan of steps. **Run:** one execution of a workflow. **Step:** one unit of work in a run, with
  an outcome.
- **Trigger:** the event that started a run (a file uploaded, a message received, a request).
- **Steps create, read, update and delete records, and call methods on records.** That is all a step does to
  data. A tag is a record, and a tag on a record is a link record: setting or removing a flag is creating or
  deleting that link, like any other change.
- **A run remembers the records each step worked with.** A record can have several runs; a run can touch several
  records.
- **A run is held by its current step.** A step can hold the run in three ways: it asked a
  [decision](../../decisions/docs/design.md) and waits for a human or another agent to answer; it waits for
  another run to finish; or it stopped on an error. Holding is always the state of a step, never of the run
  apart from its steps. The next steps act on the result (for example, set a flag after an answer).
- **Timeline of a record:** the runs that worked on the record, in order: the trigger, the steps done with their
  outcomes, the current step with its open decisions, and the steps still planned. Every entry links to the
  records that step worked with.

Rules:

1. Business rules live with the record's owner. A step calls owners; it does not hold rules.
2. People can change records directly at any time. Runs do not own the records they work on.
3. A run moves unless its current step holds it. It ends when its plan is done or a person stops it.
4. A person can stop a run at any point and do the work by hand. Stopping withdraws the run's open decisions, so
   nothing keeps asking for attention; the record stays as it is.
5. A run does not ask for a final verdict. When its plan is done the record is ready, and what happens next (for
   example posting it) is the record's own action.
6. The timeline is a view. It stores nothing of its own.

Example: a file arrives (trigger); a step decodes it, falling back to text recognition; a step creates a draft
record from it; a step matches the sender and asks to confirm the match; a step asks to confirm two uncertain
fields; a review step finds the amount unusual, sets a flag on the draft and asks whether to clear it. The run
ends with the draft ready. At any point the person could have stopped the run and finished the draft by hand.
The timeline of the draft shows all of this, past and planned.

## Implementation

How the ontology maps to this addon.

- **Records of a step:** every step records the records it read, created, changed, deleted or called (one mechanism;
  today `StepArtifact` and `WorkflowRunEvidence` each cover part of it). The timeline and its links come from this.
- **Asking:** the existing ask-and-wait step outcome creates a decision with its concern links and resumes on the
  answer. Step authors get one small call for it; no review-specific step classes per consumer.
- **Stopping:** one action on a run, offered in the timeline: cancel the run and withdraw its open decisions
  through the decisions owner.
- **Flags:** nothing in this addon. A step creates or deletes a tag assignment through the
  [tags addon](../../tags/) as it would any record.
- **Timeline read:** one GraphQL read for a record: its runs and their ancestors, each as the run graph that
  already exists (`schema.py`, the run graph types), extended with the step's records and decisions.
- **Linear order from a branching plan:** steps that ran, then the current steps, then planned steps; a planned
  step every path goes through is shown as certain, the others as one "may also" line.

UI, in this addon's web fragment:

- **Record timeline:** one component with one input, a record or a set of records. It is a pane beside the
  content and can be placed left or right.
  - Beside a record: the vertical stepper described above, with decision cards inline.
  - Beside a record set (a list or a selection): the open decisions and active runs across those records, grouped,
    each linking to its record.
- It replaces the separate runs tab and decisions tab on a record.

Not built in the first version: timers on decisions, automatic answers, any consumer-specific timeline.

## Open

- A run's records: one relation replacing `StepArtifact` and `WorkflowRunEvidence`, or the two kept and read
  together? Recommended: one.
- Left placement needs a pane host on the left of the record layout; the first version can ship on the right.
