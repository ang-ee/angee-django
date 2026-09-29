# Workflow runs

This fragment provides the workflow catalogue, retained execution evidence, and
operator actions. The Workflows rail opens runs; the catalogue lists definitions, published versions,
and their recent runs. The studio is a separate, later surface.

Runs retain the backend's origin, outcomes, attempt evidence, wait reasons, and
record references. The backend supplies execution rank and mapped-step identity;
the shared resource list pages the ordered transcript. Metadata owns record names,
labels and navigation, and the JSON viewer displays retained inputs and outputs.

Operator dialogs send cancel, reprocess, retry, and explicitly acknowledged
duplicate-risk retry requests through generated actions and `useActionForm`.
The backend's capability facts decide which controls appear. One selected action
mounts one dialog, with acknowledgement validated by the form resolver. Successful
cancel/retry messages use the shared toast; a successful reprocess links to the
replacement run. Retained errors are evidence, and readable attempts display only
the stack traces returned by the backend's field policy.

Workflows contributes the waiting run to the decisions origin slot. Records gain
a separate Workflows chatter tab scoped by model label and public ID. The existing
activity feed has no contribution slot, so these runs are not merged into that feed.
Collection state, paging, dialogs, errors, and record chrome stay with their
framework owners.

Documents use the console schema. Generate the composed host's documents before
typechecking or running the native provider tests. Stories and provider tests exercise recovery,
redacted evidence, failures, catalogue navigation, and record/decision context.
