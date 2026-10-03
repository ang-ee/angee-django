# Workflow runs

This fragment provides the workflow catalogue, retained execution evidence, and
operator actions. The Workflows rail opens runs; the catalogue lists definitions, published versions,
and their recent runs. The studio edits and publishes workflow drafts.

The default Graph run tab draws the pinned version and complete map progress through
the shared read-only canvas. Selecting a node stores `node` in the URL and opens the
shell inspector with node-scoped step evidence, checkpoint, attempts and decisions.
Run changes refresh the graph and inspector summary, including IO claims. StepRun
lists still lack live refresh: the missing StepRun `changes()` root is deferred debt.

Runs retain the backend's origin, outcomes, attempt evidence, wait reasons, and
record references. The backend supplies execution rank and mapped-step identity;
the shared resource list pages the ordered steps and opens one selected step in
the framework drawer. Attempts and artifacts are child collections. Metadata owns
record names, labels and navigation; the JSON widget displays inputs and outputs.

Declared `Action`s send cancel, reprocess, retry, and explicitly acknowledged
duplicate-risk retry requests through generated mutations. The backend's capability
facts decide which actions appear. The shared action owner handles confirmation,
typed acknowledgement, errors and pending state. Reprocess navigates to the
replacement through the resource route owner. Retained errors are evidence, and readable attempts display only
the stack traces returned by the backend's field policy.
Trigger enablement uses the same action confirmation surface to show prospective
principal grants and workflow run readers from the server's authorized preview.

Workflows contributes the waiting run as a `decisions#origin` child. Records gain
a separate Workflows chatter tab (`record#aside/workflows.runs`) scoped by model
label and public ID. The existing activity feed takes no contributed entries, so
these runs are not merged into that feed.
Routed `ResourceList` declarations own collection state, filters, grouping, paging
and the `Form` record frame. Catalogue versions and the shared runs list render in
record tabs; contextual run collections reuse that same runs declaration.

Documents use the console schema. Generate the composed host's documents before
typechecking or running the native provider tests. Stories and provider tests exercise recovery,
redacted evidence, failures, catalogue navigation, and record/decision context.
