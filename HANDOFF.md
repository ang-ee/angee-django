# px-approach handoff (plan 27)

## Built

- The topic grid now uses readable proposal columns only, person names and avatars, a single answer audience summary in each header, and an audience chip on each filled answer. Draft state is absent from the header. Empty topic rows span columns when no one can write; a writable responder cell instead offers an in-cell prompt and editor.
- Answer creation uses the declared Answer resource. Answer edits send `expected_revision`; an audience change after a body edit uses the revision returned by that update. The manager's audience chip composes the existing visibility verb and the server's `allowed_visibility`.
- The default grid contains topics only. Hosts can still request specific offer facts with `facts`. `ProposalStatements` is exported as a separate card presentation, with an inline, revision-checked statement composer for the responder.
- Both sections use `FormView.SectionHeading`. Column, fact, card and composer copy is in proposals vocabulary; section heading, hint and audience can be passed as props. The comparison and statements loading states have shaped skeletons.

## Files

- Changed: `addons/angee/proposals/README.md`; `web/src/{RoundComparison.stories.tsx,RoundComparison.test.tsx,comparison-body.tsx,comparison-campaign.test.tsx,comparison-data.ts,comparison-grid.tsx,comparison-model.test.ts,comparison-model.ts,i18n.ts,index.tsx}`.
- Added: `web/src/{comparison-interactions.test.tsx,comparison-writes.test.tsx,comparison-writes.ts,proposal-statements.tsx}`.
- No backend files or dependencies changed.

## Tests and checks

- Added tests for host headings, person headers, own versus other empty cells, a revision-aware in-cell save, manager-only audience control, statement cards and filing, single-column pre-disclosure reads, empty-row spanning, and the resource write payloads.
- Ran `git diff --check` (pass). The lane has no `node_modules`; per the common rules, no pnpm install, typecheck or Vitest was run here. No dev stack or browser was started.
- Reconciler: run composed codegen, the proposals fragment typecheck and Vitest, then the normal composed fragment checks after merge.

## Host interface and browser pass

- A host's separate tab imports `ProposalStatements` and `useRoundComparisonData(roundId)`, then passes `data.proposals` and `loading={data.fetching}`. The host can supply section heading props and relabel the composer/audience through proposals vocabulary. The existing project tab list is owned by the host.
- Check at 1280 px as manager and responder before and after disclosure: the server supplies only readable columns; empty rows and the own prompt align; the editor saves within the cell; the audience menu appears only for a manager; a stale revision leaves an inline error; the separate statements tab shows one card per readable responder and files a statement. Compare the two legacy Approach captures named in the brief.

## Owner follow-up

- The Answer resource's create metadata offers every `AnswerVisibility` enum member, including `sealed`. Current Answer permissions allow a responder to create that value but do not grant the responder read access to a sealed answer. After such a create, the answer can disappear from that responder's grid while the unique proposal/topic pair prevents another create. The Answer/permission owner needs a server-owned create-choice projection or a read rule that preserves the author's access; this lane cannot repair that in its assigned files. Verify the behavior during the browser pass.
