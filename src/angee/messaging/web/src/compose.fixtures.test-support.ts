import type { DocumentType } from "@angee/gql/console";
import { defineAngeeSchemaMetadata, type DataResourceMetadata, type Row } from "@angee/metadata";
import { ServerUnavailableError } from "@angee/refine";
import {
  GraphQLError,
  storySchema,
  type GraphQLWireOperation,
  type GraphQLWireRequest,
} from "@angee/storybook/testing";
import { baseIcons, createRouteHref, defaultWidgets, type AppRuntime } from "@angee/ui";

import type { ComposeData } from "./compose";
import { CHANNEL_MODEL, type ThreadTranscriptDocument } from "./documents";
import recordedFixtures from "./compose.fixtures.recorded.json";
import messaging from "./index";

/**
 * The composer's workshop and test fixture, over refine's GraphQL wire fake.
 *
 * Every value the server words or decides is in `compose.fixtures.recorded.json`, which
 * `tests/test_messaging_compose_console_fixture.py` records from the framework's test host
 * through the page's own documents and checks for drift; re-record it with the command in
 * `docs/checks.md`, never edit it. The shapes the page's documents read are typed from their
 * generated results, so a schema change that moves them fails the type-check.
 *
 * The fake answers as the recording says for the seat it reads as, which a story or a test
 * may switch (`as`). `compose_message` is answered by seat and by the variables without the
 * key, a scheduled send matched by having a time, since the recording fixes times; the keys a
 * seat sent are remembered per seat, so a key seen before replays its first answer whatever the
 * hold (as `compose` does), and other content under it answers the recorded conflict. A written reply's conversation reads as the
 * recording read it after the reply. `loseNextAnswer` makes the next compose write and then lose
 * its answer (a transport error with no GraphQL answer); `refuseNextAsUnsent` refuses the next
 * one before it is sent (`ServerUnavailableError`); `holdNextAnswer` keeps the next one waiting
 * until it is released, so a test can leave while it sends. A read or a send the recording has no answer
 * for is answered with a GraphQL error naming it.
 */

type Seat = "ana" | "ben" | "cleo" | "dana";
/** A seat the stories read as; `anaRevoked` is Ana once her right to send on Support is revoked. */
export type ComposerSeat = Seat | "anaRevoked";
type Conversation = "support" | "sales";

interface RecordedError {
  message: string;
  path: readonly (string | number)[];
  extensions: { code?: string };
}

type RecordedAnswer<TData> = { data: TData } | { error: RecordedError };

type TranscriptData = DocumentType<typeof ThreadTranscriptDocument>;
type ChannelRow = { id: string; display_name: string };

interface RecordedSend {
  seat: ComposerSeat;
  variables: Readonly<Record<string, unknown>>;
  answer: RecordedAnswer<ComposeData>;
  /** The conversation a written reply went on, as the seat read it after it. */
  conversation?: Conversation;
  transcript?: RecordedAnswer<TranscriptData>;
}

interface RecordedFixtures {
  metadata: Readonly<Record<string, unknown>>;
  seats: Readonly<Record<Seat, {
    channels: readonly ChannelRow[];
    sendable: readonly ChannelRow[];
    recipientLimit: number;
    transcripts: Readonly<Record<Conversation, RecordedAnswer<TranscriptData>>>;
  }>>;
  messages: Readonly<Record<string, string>>;
  channels: Readonly<Record<string, string>>;
  threads: Readonly<Record<Conversation, string>>;
  sends: readonly RecordedSend[];
  /** What `compose_message` answers other content under a key an earlier version spent. */
  conflict: RecordedAnswer<never>;
  messagePage: Readonly<Record<"cleo" | "ana", Readonly<Record<"failed" | "uncertain", RecordedAnswer<{ messages_by_pk: Row | null }>>>>>;
}

/** What the recorder wrote: JSON, so its values are checked by the recorder's own test, its shapes by these types. */
export const RECORDED = recordedFixtures as unknown as RecordedFixtures;

function recordedResource(model: string): DataResourceMetadata {
  const resource = defineAngeeSchemaMetadata({ angee: { resources: [RECORDED.metadata[model]] } }).angee?.resources?.[0];
  if (!resource) throw new Error(`The recorded fixtures carry no ${model} resource.`);
  return resource;
}

const MESSAGE_RESOURCE = recordedResource("messaging.Message");
const CHANNEL_RESOURCE = recordedResource(CHANNEL_MODEL);
// The message page's Content tab lists the message's parts.
const PART_RESOURCE = recordedResource("messaging.Part");

/** The recorded messages, conversations and channels, by the names the recorder gives them. */
export const MESSAGE = RECORDED.messages;
export const THREAD = RECORDED.threads;
export const CHANNEL = RECORDED.channels;

/** The recorded variables of one send, by seat and the text it sends (each recorded send's text is its own). */
export function recordedSend(seat: ComposerSeat, text: string): RecordedSend {
  const send = RECORDED.sends.find((entry) => entry.seat === seat && entry.variables.text === text);
  if (!send) throw new Error(`The recorded fixtures hold no send of "${text}" by ${seat}.`);
  return send;
}

/** One root field the fake answered, with the arguments it was asked with and the seat it read as. */
export interface ComposerFixtureCall {
  field: string;
  args: Readonly<Record<string, unknown>>;
  seat: ComposerSeat;
}

export interface ComposerFixtureOptions {
  seat?: ComposerSeat;
  onRequest?: (request: GraphQLWireRequest) => void;
  onCall?: (call: ComposerFixtureCall) => void;
}

/** The fake's controls for a story or a test, beside the schemas it mounts. */
export interface ComposerFixture {
  schemas: Record<string, ReturnType<typeof storySchema>[string]>;
  /** Read as another seat from now on. */
  as: (seat: ComposerSeat) => void;
  /** The next `compose_message` writes what the recording says, then its answer is lost. */
  loseNextAnswer: () => void;
  /** The next `compose_message` is refused before it is sent, as the connection boundary refuses it. */
  refuseNextAsUnsent: () => void;
  /** The next `compose_message` waits to be answered until the returned release is called. */
  holdNextAnswer: () => () => void;
}

function recordedError(error: RecordedError): GraphQLError {
  return new GraphQLError(error.message, { path: error.path, extensions: error.extensions });
}

/** The answer to a read or a send the recording holds none for: a GraphQL error, which the client does not retry. */
function unrecorded(what: string): GraphQLError {
  return new GraphQLError(`The recorded fixtures hold no answer for ${what}.`);
}

function answered<TData>(answer: RecordedAnswer<TData>): TData {
  if ("error" in answer) throw recordedError(answer.error);
  return answer.data;
}

/** The variables of a send as the fake finds its recorded answer: without the key, a time stood for by having one. */
function sendShape(variables: Readonly<Record<string, unknown>>): string {
  return JSON.stringify(Object.entries(variables)
    .filter(([name, value]) => name !== "idempotency_key" && value != null)
    .map(([name, value]) => [name, name === "scheduled_at" ? true : value] as const)
    .sort(([left], [right]) => left.localeCompare(right)));
}

/** What `compose` fingerprints under a key: the send without its key and its hold (`queued`, `scheduled_at`). */
function intentShape(variables: Readonly<Record<string, unknown>>): string {
  return sendShape(Object.fromEntries(Object.entries(variables).filter(([name]) => name !== "queued" && name !== "scheduled_at")));
}

/** What a channels read's `where` asks: the `sendable` channels or every readable one, by id and label search. */
function channelRows(rows: readonly ChannelRow[], where: unknown): readonly ChannelRow[] {
  let ids: readonly string[] | null = null;
  let search: string | null = null;
  const walk = (node: unknown): void => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) return node.forEach(walk);
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      const condition = value as Record<string, unknown> | null;
      if (key === "id" && condition && Array.isArray(condition._in)) ids = condition._in as string[];
      else if (key === "id" && condition && typeof condition._eq === "string") ids = [condition._eq];
      else if (key === "display_name" && condition && typeof (condition._ilike ?? condition._iLike) === "string") {
        search = String(condition._ilike ?? condition._iLike).replaceAll("%", "").toLowerCase();
      } else walk(value);
    }
  };
  walk(where);
  return rows.filter((row) => (ids === null || ids.includes(row.id))
    && (search === null || row.display_name.toLowerCase().includes(search)));
}

function asksSendable(where: unknown): boolean {
  return JSON.stringify(where ?? null).includes('"sendable"');
}

/** The schemas a story or a test mounts: the console over the fake, with the recorded metadata. */
export function composerFixture({ seat: initial = "ana", onRequest, onCall }: ComposerFixtureOptions = {}): ComposerFixture {
  let seat: ComposerSeat = initial;
  let loseNext = false;
  let refuseNext = false;
  let held: Promise<void> | null = null;
  const keys = new Map<ComposerSeat, Map<string, { intent: string; answer: ComposeData }>>();
  const transcripts = new Map<string, RecordedAnswer<TranscriptData>>();
  // `anaRevoked` reads as Ana did: only what composing answers changed with her right to send.
  const reads = () => RECORDED.seats[seat === "anaRevoked" ? "ana" : seat];
  const conversation = (threadId: unknown): Conversation => {
    const name = (Object.keys(RECORDED.threads) as Conversation[]).find((key) => RECORDED.threads[key] === threadId);
    if (!name) throw unrecorded(`the conversation ${String(threadId)}`);
    return name;
  };
  const transcript = (threadId: unknown): TranscriptData => {
    const name = conversation(threadId);
    return answered(transcripts.get(`${seat}:${name}`) ?? reads().transcripts[name]);
  };
  const channels = (where: unknown) => channelRows(asksSendable(where) ? reads().sendable : reads().channels, where);
  let shown: Row | null = null;
  // Cleo reads the message page as its channel's manager; every other seat as Ana, who wrote on it.
  const pageRow = (id: unknown): Row | null => {
    const state = (["failed", "uncertain"] as const).find((name) => RECORDED.messages[name] === id);
    if (!state) throw unrecorded(`the message page of ${String(id)}`);
    shown = answered(RECORDED.messagePage[seat === "cleo" ? "cleo" : "ana"][state]).messages_by_pk;
    return shown;
  };
  const pageParts = (): readonly Row[] => (Array.isArray(shown?.parts) ? shown.parts as Row[] : []);
  const operations: Record<string, GraphQLWireOperation> = {
    channels: ({ where, limit }) => channels(where).slice(0, typeof limit === "number" ? limit : undefined),
    channels_aggregate: ({ where }) => ({ aggregate: { count: channels(where).length } }),
    // A read-only Channel labels its one channel by id.
    channels_by_pk: ({ id }) => reads().channels.find((row) => row.id === id) ?? null,
    message_recipient_limit: () => reads().recipientLimit,
    thread_message_feed: ({ thread_id }) => transcript(thread_id).thread_message_feed,
    thread_message_feed_revalidate: ({ thread_id, ids }) => {
      const messages = transcript(thread_id).thread_message_feed.messages;
      const wanted = Array.isArray(ids) ? ids.map(String) : [];
      return {
        messages: messages.filter((message) => wanted.includes(message.id)),
        absent_ids: wanted.filter((id) => !messages.some((message) => message.id === id)),
      };
    },
    // The message page's form and its reader read the one recorded row, and its Content tab the row's parts.
    messages_by_pk: ({ id }) => pageRow(id),
    messages: ({ where }) => {
      const row = pageRow((where as { id?: { _eq?: unknown } } | undefined)?.id?._eq);
      return row ? [row] : [];
    },
    parts: () => pageParts(),
    parts_aggregate: () => ({ aggregate: { count: pageParts().length } }),
    compose_message: async (args) => {
      if (held) {
        const waiting = held;
        held = null;
        await waiting;
      }
      const key = String(args.idempotency_key);
      const shape = sendShape(args);
      const sent = keys.get(seat) ?? new Map();
      keys.set(seat, sent);
      const seen = sent.get(key);
      if (seen) {
        if (seen.intent !== intentShape(args)) answered(RECORDED.conflict);
        return seen.answer.compose_message;
      }
      if (refuseNext) {
        refuseNext = false;
        throw new ServerUnavailableError();
      }
      const send = RECORDED.sends.find((entry) => entry.seat === seat && sendShape(entry.variables) === shape);
      if (!send) throw unrecorded(`compose_message as ${seat} with ${shape}`);
      const data = answered(send.answer);
      if (data.compose_message?.ok) {
        sent.set(key, { intent: intentShape(args), answer: data });
        if (send.conversation && send.transcript) transcripts.set(`${seat}:${send.conversation}`, send.transcript);
      }
      if (loseNext) {
        loseNext = false;
        throw new Error("The connection dropped before the answer arrived.");
      }
      return data.compose_message;
    },
  };
  const observed = Object.fromEntries(Object.entries(operations).map(([field, operation]) => [
    field,
    ((args, request) => {
      onCall?.({ field, args, seat });
      return operation(args, request);
    }) satisfies GraphQLWireOperation,
  ]));
  const fixture = storySchema(observed, onRequest).public!;
  return {
    schemas: {
      public: fixture,
      console: { ...fixture, metadata: { angee: { resources: [MESSAGE_RESOURCE, CHANNEL_RESOURCE, PART_RESOURCE] } } },
    },
    as: (next) => { seat = next; },
    loseNextAnswer: () => { loseNext = true; },
    refuseNextAsUnsent: () => { refuseNext = true; },
    holdNextAnswer: () => {
      let release = () => undefined as void;
      held = new Promise((done) => { release = done; });
      return release;
    },
  };
}

/** The addon's route hrefs, from its own routes. */
export const COMPOSER_ROUTE_HREF = createRouteHref(messaging.routes ?? []);

/** The runtime a story or a test mounts for one seat: messaging's routes, glyphs and the shared widgets; `preview`, the seat seen by another. */
export function composerRuntime(seat: ComposerSeat = "ana", preview = false): Partial<AppRuntime> {
  const user = { id: `usr_${seat}`, name: seat, permitted: [] };
  return {
    routeHref: COMPOSER_ROUTE_HREF,
    routesByResource: {
      "messaging.Message": { collection: "messaging.messages", record: { name: "messaging.messages.record", param: "id" } },
      [CHANNEL_MODEL]: { collection: "messaging.channels", record: { name: "messaging.channels.record", param: "id" } },
    },
    widgets: defaultWidgets,
    icons: { ...baseIcons, ...messaging.icons },
    auth: {
      status: "authenticated",
      user,
      hasRole: () => false,
      ...(preview ? { viewAs: {
        viewAs: { userId: user.id }, currentUser: user, realUser: { id: "usr_admin", name: "Administrator", permitted: [] },
        viewablePeople: [user], enter: () => undefined, exit: () => undefined,
      } } : {}),
    },
  };
}
