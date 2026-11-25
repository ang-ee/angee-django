import { useMemo } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import {
  RoutedRuntimeFixture, storyAppearance, type GraphQLWireRequest, type RuntimeFixtureProps,
} from "@angee/storybook/testing";
import { REFINE_CREATE_ID } from "@angee/ui";

import { MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATES_ROUTE } from "../resources";
import {
  MESSAGE_TEMPLATE_RECORD_ROUTE, MESSAGE_TEMPLATE_ROUTE_HREF, ROW, messageTemplateRuntime, messageTemplateSchemas,
  type MessageTemplateFixtureCall, type MessageTemplateViewer,
} from "../fixtures.test-support";
import { MessageTemplatesPage } from "./MessageTemplatesPage";

export default {
  title: "Message templates/Library",
  parameters: { angeeOwnRuntime: true, layout: "fullscreen" },
  excludeStories: ["MessageTemplateStory"],
};

type StoryContext = { globals: Record<string, unknown> };

/** The library: every row, grouped by kind. Open a row to read it beside the list. */
export const Library = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} /> };
/** A shipped template nobody overrides: read-only, the notice, Override, and its Preview. Override it: the override opens. */
export const ShippedDefault = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.welcome} /> };
/** A current override of a shipped block: editable, no Message section, and Delete in the record's menu. */
export const Override = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.quote} /> };
/** An override whose shipped default changed: the warning, Keep override and Delete. Keep it: the warning goes. */
export const DefaultUpdated = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.recordCard} /> };
/** Every save is refused as the console refused a template set to HTML with no layout: choose HTML in Format, empty Layout and save. One banner holds every reason, run together. */
export const RefusedSave = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} refuseSave /> };
/** New: the name, kind, key and language, nothing else. Create Template, example.announcement, en, Announcement: the seeded template opens, its identity fixed. */
export const NewMessageTemplate = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open="new" /> };
/** New, refused for a shipped default's identity: create Layout, base, en, Base. */
export const CreateRefusedShipped = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open="new" /> };
/** New, refused for an identity the deployment has: create Block, notice, en, Notice. */
export const CreateDuplicate = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open="new" /> };
/** New, refused under the key: create Block, Not A Key, en, Bad key. */
export const CreateBadKey = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open="new" /> };
/** A template whose preview warns: an empty preheader and a value nothing reads. */
export const PreviewWarnings = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.passwordReset} /> };
/** A template whose sample no longer renders: each reason as the server words it, and no frame. */
export const PreviewRenderError = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.invoiceSent} /> };
/** Choose Sandstone in Brand kit: the preview reads again, in that kit's colours, fonts and footer. */
export const PreviewOtherKit = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.welcome} /> };
/** The deployment's own Arabic template: the frame's document reads right to left, and so does its plain text. */
export const RightToLeft = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.welcomeArabic} /> };
/** The deployment's own block an override includes: Delete in the record's menu shows the server's refusal. */
export const DeleteRefused = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.notice} /> };
/** Keep override is refused, as for an override whose shipped default is gone: a toast with the server's words. */
export const KeepRefused = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.recordCard} refuseKeep /> };
/** A member: the facts alone in the notice, no verb, no Preview, the sample as a restriction, no Backlinks. */
export const MemberView = { render: (_args: unknown, { globals }: StoryContext) => <MessageTemplateStory appearance={storyAppearance(globals)} open={ROW.invoiceSent} viewer="member" /> };

const documents = { console: operationDocuments };

/**
 * The real Message templates page and message template form over the console wire
 * fake (`messageTemplateSchemas`), in the routed console shell with the addon's
 * routes, containers, presets and glyph.
 */
export function MessageTemplateStory({
  appearance, open, viewer = "admin", refuseSave = false, refuseKeep = false, onRequest, onCall,
}: {
  appearance?: RuntimeFixtureProps["appearance"];
  /** The row whose record opens, or `new` for the create form; the library when absent. */
  open?: string;
  viewer?: MessageTemplateViewer;
  refuseSave?: boolean;
  refuseKeep?: boolean;
  onRequest?: (request: GraphQLWireRequest) => void;
  onCall?: (call: MessageTemplateFixtureCall) => void;
}) {
  const schemas = useMemo(
    () => messageTemplateSchemas({ viewer, refuseSave, refuseKeep, onRequest, onCall }),
    [viewer, refuseSave, refuseKeep, onRequest, onCall],
  );
  const collectionPath = MESSAGE_TEMPLATE_ROUTE_HREF(MESSAGE_TEMPLATES_ROUTE);
  const initialEntry = open
    ? MESSAGE_TEMPLATE_ROUTE_HREF(MESSAGE_TEMPLATE_RECORD_ROUTE, { id: open === "new" ? REFINE_CREATE_ID : open })
    : collectionPath;
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas}
    collectionPath={collectionPath} initialEntry={initialEntry} runtime={messageTemplateRuntime(viewer)}
    resourceName={MESSAGE_TEMPLATE_MODEL} resourceLabel="Message templates" operationDocuments={documents}>
    <MessageTemplatesPage />
  </RoutedRuntimeFixture>;
}
