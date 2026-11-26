import * as React from "react";
import { AuthStateProvider } from "@angee/app";
import { RoutedRuntimeFixture, storyAppearance, type GraphQLWireRequest, type RuntimeFixtureProps } from "@angee/storybook/testing";
import { PaneToolbarHost, RegisteredFormView, pathnameChanges } from "@angee/ui";

import {
  COMPOSER_ROUTE_HREF, MESSAGE, THREAD, composerFixture, composerRuntime,
  type ComposerFixture, type ComposerFixtureCall, type ComposerSeat,
} from "./compose.fixtures.test-support";
import { messageForm } from "./MessageForm";
import { NewMessageAction } from "./NewMessageAction";
import { ThreadTranscript, type ThreadTranscriptReply } from "./ThreadTranscript";

export default {
  title: "Messaging/Compose",
  parameters: { angeeOwnRuntime: true, layout: "fullscreen" },
  excludeStories: ["ComposerStory"],
};

type StoryContext = { globals: Record<string, unknown> };
type Surface = "new-message" | "transcript" | "message-page";

const REPLY: ThreadTranscriptReply = { leaves: pathnameChanges };

// Each story shows its state as it mounts: a fragment's stories have no `play` (that needs the third-party
// `storybook/test`, which no fragment declares), so a state reached only by typing or clicking is the native
// tests', and the workshop reaches it by hand from the story that opens on it.

/** Ana's New message, as the inbox header shows it: she sends on Support alone. Open it: Channel is fixed and focus starts in To; type "ana" and Send for a refusal under To, or an address and a message for the server's toast. */
export const NewMessage = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="new-message" seat="ana" /> };
/** The Support conversation for Ana: Reply on each email of it, and the delivery of what was written here. Reply on an email: the box docks under the conversation and sends with Ctrl/Cmd+Enter; "Who can see this?" is refused for its Reply-To. */
export const Reply = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" /> };
/** The Sales conversation for Ana, who reads it and may not send on it: no Reply. */
export const ReplyGateReader = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" conversation="sales" /> };
/** The Sales conversation for Dana, who may send on it: Reply on its email. */
export const ReplyGateSender = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="dana" conversation="sales" /> };
/** The Support conversation in a 420 px column: rows, their delivery and their actions wrap. */
export const ConversationNarrow = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" width={420} /> };
/** Every local delivery state on Support's outgoing mail, with its time and attempts, Delivery details on Failed and Uncertain; synced own mail and the inbound requests show none. A host that offers no Reply (a record aside) shows the same. */
export const DeliveryStates = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="cleo" noReply /> };
/** The failed message's Envelope for Cleo, who manages Support: why delivery failed, the attempts, and Retry delivery. */
export const FailedForManager = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="message-page" seat="cleo" message={MESSAGE.failed} pane="envelope" /> };
/** The failed message's Envelope for Ana, who wrote on Support: the attempts, no reason and no verb. */
export const FailedForAuthor = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="message-page" seat="ana" message={MESSAGE.failed} pane="envelope" /> };
/** The uncertain message's Envelope for Cleo: the reason, Retry delivery and Mark as sent. */
export const UncertainForManager = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="message-page" seat="cleo" message={MESSAGE.uncertain} pane="envelope" /> };
/** Ben reads Support and may send nowhere: no New message and no Reply; everything reads as it did. */
export const ReaderCannotSend = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ben" withNewMessage /> };

/** The real New message action, transcript and message page over the composer's wire fake, in the routed console shell. */
export function ComposerStory({
  appearance, surface, seat = "ana", conversation = "support", anchor = "", message, pane, width, noReply = false, withNewMessage = false,
  lose = false, revokeAfterSend = false, preview = false, onSent, onRequest, onCall, onFixture, queryClient,
}: {
  appearance?: RuntimeFixtureProps["appearance"];
  surface: Surface;
  seat?: ComposerSeat;
  conversation?: "support" | "sales";
  /** The transcript's window, as a host anchors it (`message:<id>`); it moves as the transcript asks. */
  anchor?: string;
  /** The message whose page opens (`message-page`). */
  message?: string;
  /** The message page's pane it opens on (`envelope`). */
  pane?: string;
  /** A column this wide, in pixels. */
  width?: number;
  /** A transcript host that offers no Reply. */
  noReply?: boolean;
  /** The transcript with the New message action above it. */
  withNewMessage?: boolean;
  /** The first send's answer is lost. */
  lose?: boolean;
  /** After the first send, the seat's right to send on Support is revoked. */
  revokeAfterSend?: boolean;
  /** An administrator previews the seat: nothing is sent. */
  preview?: boolean;
  onSent?: (id: string) => void;
  onRequest?: (request: GraphQLWireRequest) => void;
  onCall?: (call: ComposerFixtureCall) => void;
  /** The fake's controls, for a test that moves it on (another seat, a lost answer). */
  onFixture?: (fixture: ComposerFixture) => void;
  queryClient?: RuntimeFixtureProps["queryClient"];
}): React.ReactElement {
  const fixture = React.useMemo(() => {
    let sent = 0;
    const made = composerFixture({
      seat, onRequest,
      // A call is observed before it is answered: the second send is the first answered as revoked.
      onCall: (call) => {
        onCall?.(call);
        if (call.field !== "compose_message") return;
        sent += 1;
        if (revokeAfterSend && sent === 2) made.as("anaRevoked");
      },
    });
    if (lose) made.loseNextAnswer();
    return made;
  }, [lose, onCall, onRequest, revokeAfterSend, seat]);
  React.useEffect(() => { onFixture?.(fixture); }, [fixture, onFixture]);
  const [at, setAt] = React.useState(anchor);
  const collectionPath = surface === "message-page" ? COMPOSER_ROUTE_HREF("messaging.messages") : COMPOSER_ROUTE_HREF("messaging.threads");
  const content = surface === "new-message"
    ? <div className="flex items-center gap-2 p-4"><NewMessageAction onSent={onSent} /></div>
    : surface === "message-page"
      ? <RegisteredFormView resource="messaging.Message" id={message} defaultRecordTab={pane} />
      : <div className="flex h-[40rem] min-h-0 flex-col">
        {withNewMessage ? <div className="flex justify-end p-2"><NewMessageAction onSent={onSent} /></div> : null}
        <PaneToolbarHost>
          <ThreadTranscript threadId={THREAD[conversation]} anchor={at} onAnchorChange={setAt} reply={noReply ? undefined : REPLY} />
        </PaneToolbarHost>
      </div>;
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={fixture.schemas}
    collectionPath={collectionPath} runtime={{ ...composerRuntime(seat, preview), forms: { "messaging.Message": messageForm } }}
    resourceName={surface === "message-page" ? "messaging.Message" : undefined} queryClient={queryClient}>
    {/* The transcript reads its feed as the signed-in person. */}
    <AuthStateProvider auth={{ user: { id: `usr_${seat}`, name: seat }, status: "authenticated", hasRole: () => false }}>
      <div style={width ? { width } : undefined} className="min-h-0">{content}</div>
    </AuthStateProvider>
  </RoutedRuntimeFixture>;
}
