import { useMemo } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import {
  RoutedRuntimeFixture, storyAppearance, type GraphQLWireRequest, type RuntimeFixtureProps,
} from "@angee/storybook/testing";
import { REFINE_CREATE_ID } from "@angee/ui";

import { MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATES_ROUTE } from "../resources";
import {
  MESSAGE_TEMPLATE_RECORD_ROUTE, MESSAGE_TEMPLATE_ROUTE_HREF, ROW, messageTemplateRuntime, messageTemplateSchemas,
  type MessageTemplateFixtureCall, type MessageTemplateViewer, type RecordedSaveRefusal,
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
  failPreviewAfterSave = false, onRequest, onCall, queryClient,
}: {
  appearance?: RuntimeFixtureProps["appearance"];
  /** The row whose record opens, or `new` for the create form; the library when absent. */
  open?: string;
  viewer?: MessageTemplateViewer;
  refuseSave?: RecordedSaveRefusal;
  refuseKeep?: boolean;
  overrideDenied?: boolean;
  failPreviewAfterSave?: boolean;
  onRequest?: (request: GraphQLWireRequest) => void;
  onCall?: (call: MessageTemplateFixtureCall) => void;
  /** A test's own query client (its retry policy, say); the fixture makes one when absent. */
  queryClient?: RuntimeFixtureProps["queryClient"];
}) {
  const schemas = useMemo(
    () => messageTemplateSchemas({
      viewer, refuseSave, refuseKeep, overrideDenied, failPreviewAfterSave, onRequest, onCall,
    }),
    [viewer, refuseSave, refuseKeep, overrideDenied, failPreviewAfterSave, onRequest, onCall],
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
