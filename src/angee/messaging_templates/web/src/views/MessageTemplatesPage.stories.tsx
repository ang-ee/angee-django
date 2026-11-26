import { useMemo } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import {
  RoutedRuntimeFixture, storyAppearance, type GraphQLWireRequest, type RuntimeFixtureProps,
} from "@angee/storybook/testing";
import { REFINE_CREATE_ID } from "@angee/ui";

import { MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATES_ROUTE } from "../resources";
import {
  MESSAGE_TEMPLATE_RECORD_ROUTE, MESSAGE_TEMPLATE_ROUTE_HREF, ROW, messageTemplateRuntime, messageTemplateSchemas,
  type MessageTemplateFixtureCall, type MessageTemplateFixtureOptions, type MessageTemplateViewer,
  type RecordedSaveRefusal,
} from "../fixtures.test-support";
import { MessageTemplatesPage } from "./MessageTemplatesPage";

export default {
  title: "Message templates/Library",
  parameters: { angeeOwnRuntime: true, layout: "fullscreen" },
  excludeStories: ["MessageTemplateStory"],
};

type StoryContext = { globals: Record<string, unknown> };

/** The library: every row, grouped by kind; presets Default updated and Effective. Open a row to read it. */
export const Library = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} /> };
/** A shipped template nobody overrides: read-only, the notice, Override, and its Preview; choose Sandstone in Brand kit and the preview reads again in that kit. Override it: the deployment's override opens. */
export const ShippedDefault = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.welcome} /> };
/** A current override of a shipped block: editable, no Message section, and Delete in the record's menu. */
export const Override = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.quote} /> };
/** An override whose shipped default changed: the warning, Keep override and Delete. Keep it: the warning goes. */
export const DefaultUpdated = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.recordCard} /> };
/** Every save is refused as the console refused a template set to HTML with no layout: choose HTML in Format, empty Layout and save. The banner holds the row's reason; the layout's sits under Layout. */
export const RefusedSave = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} refuseSave="layout" /> };
/**
 * New: the name, kind, key and language, nothing else. The recorded inserts: Template, example.announcement, en,
 * Announcement opens the seeded template with its identity fixed; Layout, base, en refuses a shipped default's
 * identity, Block, notice, en an identity the deployment has, and Part, nested, de a new language seeded from a row
 * whose includes nest past the depth, each in the banner, which takes focus; Block, Not A Key, en refuses the key
 * under Key.
 */
export const NewMessageTemplate = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open="new" /> };
/** A template whose preview warns: a missing preheader and a value nothing reads, first in the band above the frame, which keeps most of the pane and scrolls the mail. */
export const PreviewWarnings = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} /> };
/** A template whose sample no longer renders: that it does not render, then each reason as the server words it; no frame. */
export const PreviewRenderError = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.invoiceSent} /> };
/** Password reset with an unsaved Subject: paste `Your new {{ brand.name }} password` into Subject. Once the edit settles the Preview says it shows your unsaved edits and renders "Your new Angee password"; Discard, or Save, and it shows the saved row again. */
export const PreviewUnsavedEdits = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} /> };
/** Password reset with unsaved sample values that leave out the reset link: in Sample values, replace the sample with `{"person": {"first_name": "Ana"}}`, then open Preview (today the form asks whether to leave without saving, and leaving keeps the edits; it stops asking once a record tab no longer counts as leaving). It says these edits would save but do not render with these sample values, and gives the server's reason; no frame. */
export const PreviewUnsavedUnrendered = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} /> };
/** Password reset with edits Save would refuse: choose HTML in Format and empty Layout. The Preview says Save would refuse these edits and lists the server's two reasons; choose the layout's and Layout takes focus. */
export const PreviewUnsavedRefused = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} /> };
/** Password reset with lists where objects belong: in Sample values replace the sample with `[]`, and in Variables replace the variables with `[]`, then open Preview (today the form asks whether to leave without saving, and leaving keeps the edits). Save would refuse both in the same words, "Give an object.", each titled by its field's label, Variables and Sample; choose one and its field takes focus. */
export const PreviewUnsavedSameWords = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} /> };
/** The same edit where the deployment lets nobody write a layout: choose HTML in Format and empty Layout. The server refuses the preview itself, and the band shows its refusal under the unsaved-edits caption; no frame. */
export const PreviewUnsavedDenied = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} layoutGated /> };
/** The deployment's own Arabic template: the frame's document, its subject, preheader and plain text read right to left. */
export const RightToLeft = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.welcomeArabic} /> };
/** The deployment's own block an override includes: Delete in the record's menu shows the server's refusal. */
export const DeleteRefused = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.notice} /> };
/** Keep override on an override another session deleted since it opened: the toast gives the server's words, and the warning stays. */
export const KeepRefused = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.recordCard} refuseKeep /> };
/** A member on a shipped default: the fact alone in the notice, no verb, no Preview, the sample as a restriction, no Backlinks. */
export const MemberView = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.welcome} viewer="member" /> };

const documents = { console: operationDocuments };

/**
 * The real Message templates page and message template form over the console wire
 * fake (`messageTemplateSchemas`), in the routed console shell with the addon's
 * routes, containers, presets and glyph. The fake answers what the recording holds
 * and refuses anything else with a GraphQL error that names it.
 */
export function MessageTemplateStory({
  appearance, open, viewer = "admin", refuseSave, refuseKeep = false, overrideDenied = false,
  failPreviewAfterSave = false, layoutGated = false, beforePreview, onRequest, onCall, queryClient,
}: {
  appearance?: RuntimeFixtureProps["appearance"];
  /** The row whose record opens, or `new` for the create form; the library when absent. */
  open?: string;
  viewer?: MessageTemplateViewer;
  refuseSave?: RecordedSaveRefusal;
  refuseKeep?: boolean;
  overrideDenied?: boolean;
  failPreviewAfterSave?: boolean;
  layoutGated?: boolean;
  beforePreview?: MessageTemplateFixtureOptions["beforePreview"];
  onRequest?: (request: GraphQLWireRequest) => void;
  onCall?: (call: MessageTemplateFixtureCall) => void;
  /** A test's own query client (its retry policy, say); the fixture makes one when absent. */
  queryClient?: RuntimeFixtureProps["queryClient"];
}) {
  const schemas = useMemo(
    () => messageTemplateSchemas({
      viewer, refuseSave, refuseKeep, overrideDenied, failPreviewAfterSave, layoutGated, beforePreview, onRequest,
      onCall,
    }),
    [viewer, refuseSave, refuseKeep, overrideDenied, failPreviewAfterSave, layoutGated, beforePreview, onRequest,
      onCall],
  );
  const collectionPath = MESSAGE_TEMPLATE_ROUTE_HREF(MESSAGE_TEMPLATES_ROUTE);
  const initialEntry = open
    ? MESSAGE_TEMPLATE_ROUTE_HREF(MESSAGE_TEMPLATE_RECORD_ROUTE, { id: open === "new" ? REFINE_CREATE_ID : open })
    : collectionPath;
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas}
    collectionPath={collectionPath} initialEntry={initialEntry} runtime={messageTemplateRuntime(viewer)}
    resourceName={MESSAGE_TEMPLATE_MODEL} resourceLabel="Message templates" operationDocuments={documents}
    queryClient={queryClient}>
    <MessageTemplatesPage />
  </RoutedRuntimeFixture>;
}
