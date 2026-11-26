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

/** Ana sends on Support alone: Channel is fixed and focus starts in To. Type an address and a message and Send: the toast gives the server's words. */
export const NewMessage = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="new-message" seat="ana" /> };
/** Dana sends on Billing, Sales and Support: focus starts on Channel, and Send waits for a channel and a message. Billing is offered and refused at send under Channel: its sender is not an address. */
export const NewMessageChooseChannel = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="new-message" seat="dana" /> };
/** Send to "ana": the server refuses the entry under To, and the banner takes focus with the reason. */
export const NewMessageRefusedAddress = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="new-message" seat="ana" /> };
/** Fifty-one addresses: refused under To with the bound the server states, the same bound the To hint names. */
export const NewMessageTooMany = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="new-message" seat="ana" /> };
/** Send later with a time: "Message scheduled.", and the hint says only a manager of the channel can cancel it. */
export const NewMessageScheduled = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="new-message" seat="ana" /> };
/** The first Send's answer is lost: the banner says it may have been sent and that sending again from this page sends it once; Send again lands on the original. */
export const NewMessageAnswerLost = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="new-message" seat="ana" lose /> };
/** The Support conversation for Ana: Reply on each email, the box docked under the conversation, sent with Ctrl/Cmd+Enter; the new message shows Queued at the end. */
export const Reply = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" /> };
/** The Sales conversation: Ana reads it and sees no Reply; Dana, who may send on Sales, does. */
export const ReplyGate = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" conversation="sales" /> };
/** Reply to "Who can see this?": its Reply-To names no address, refused in a toast with the server's words; the text stays. */
export const ReplyRefused = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" /> };
/** The first Send's answer is lost: the box's note says so; Send again lands on the original. */
export const ReplyAnswerLost = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" lose /> };
/** The answer is lost, then Ana's right to send is revoked: the replay is refused, and the note says an earlier attempt may already have been sent. */
export const ReplyPermissionLost = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" lose revokeAfterSend /> };
/** The conversation with the box open in a 420 px column: the row actions wrap and Send stays in view. */
export const ReplyNarrow = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" width={420} /> };
/** Every local delivery state on Support's outgoing mail, with its time and attempts, Delivery details on Failed and Uncertain; synced own mail and the inbound requests show none. A host that offers no Reply (a record aside) shows the same. */
export const DeliveryStates = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="cleo" noReply /> };
/** The failed message's page for Cleo, who manages Support: why delivery failed, the attempts, and Retry delivery. */
export const FailedForManager = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="message-page" seat="cleo" message={MESSAGE.failed} /> };
/** The failed message's page for Ana, who wrote on Support: the status and the attempts, no reason and no verb. */
export const FailedForAuthor = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="message-page" seat="ana" message={MESSAGE.failed} /> };
/** The uncertain message's page for Cleo: the reason, Retry delivery and Mark as sent. */
export const UncertainForManager = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="message-page" seat="cleo" message={MESSAGE.uncertain} /> };
/** An administrator previews Ana: the box is read-only under the preview's notice, and the dialog's Send is off. */
export const PreviewSendsNothing = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ana" preview withNewMessage /> };
/** Ben reads Support and may send nowhere: no New message and no Reply; everything reads as it did. */
export const ReaderCannotSend = { render: (_args: unknown, { globals }: StoryContext) => <ComposerStory appearance={storyAppearance(globals)} surface="transcript" seat="ben" withNewMessage /> };

/** The real New message action, transcript and message page over the composer's wire fake, in the routed console shell. */
export function ComposerStory({
  appearance, surface, seat = "ana", conversation = "support", anchor = "", message, width, noReply = false, withNewMessage = false,
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
      ? <RegisteredFormView resource="messaging.Message" id={message} />
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
