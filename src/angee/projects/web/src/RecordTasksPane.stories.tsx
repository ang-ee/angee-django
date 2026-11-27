import * as React from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import {
  RoutedRuntimeFixture, storyAppearance, storySchema, testDataResource, type GraphQLWireRequest, type RuntimeFixtureProps,
} from "@angee/storybook/testing";
import { baseIcons, formatDateStorage, useLatestRef } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";

import type { RecordTaskRow, RecordTasksPayload } from "./documents";
import projects from "./index";
import { RecordTasksPane } from "./RecordTasksPane";

// A record's Tasks pane, the shipped `RecordTasksPane`, over an in-memory transport: the record's
// tasks are a small list that a schedule, a log, a completion and a drop write to, so what the pane
// sends comes back in it. `refuse` refuses every schedule or log under the keys its server refuses
// with, so where each reason lands is the pane's own renaming of those keys to its fields. A test
// DOM focuses targets a browser refuses, so where focus lands is read in a browser.

/** What every schedule or log is refused for while `refuse` names it. */
type Refusal = "nothing" | "text" | "note" | "kind" | "day" | "assignee" | "textTwice" | "everyField";
/** The kind catalog: To do and Call, or no kind. */
type Catalog = "listed" | "none";
type Verb = "schedule" | "log";
/** What a schedule or log carries that its server refuses with a reason. */
type Part = "text" | "secondText" | "note" | "kind" | "day" | "assignee";

interface RecordTasksArgs {
  refuse: Refusal;
  kinds: Catalog;
}

/** The parts each choice of `refuse` refuses; every field is each field the pane can draw. */
const REFUSALS: Record<Refusal, readonly Part[]> = {
  nothing: [],
  text: ["text"],
  note: ["note"],
  kind: ["kind"],
  day: ["day"],
  assignee: ["assignee"],
  textTwice: ["text", "secondText"],
  everyField: ["text", "note", "kind", "day"],
};

const REFUSE_LABELS: Record<Refusal, string> = {
  nothing: "Nothing",
  text: "The text",
  note: "The note",
  kind: "The kind",
  day: "The day",
  assignee: "The assignee (no field)",
  textTwice: "The text, with two reasons",
  everyField: "Every field at once",
};

/**
 * Each verb's refusal of a part: the key its server refuses it under, camel-cased by the action
 * guard, and the reason. Scheduling takes the text as `title` and the day as `dueDate`; logging
 * takes the text and the note as one `note`, so a reason about either comes back under it, and
 * refuses the day under `dueDate`. No field of the pane shows the assignee.
 */
const SERVER_REFUSALS: Record<Verb, Record<Part, readonly [key: string, reason: string]>> = {
  schedule: {
    text: ["title", "A record task needs a title."],
    secondText: ["title", "Use a title no other open task on this record has."],
    note: ["note", "The note is too long for this record."],
    kind: ["activityType", "Choose a known kind."],
    day: ["dueDate", "The day falls in a closed period."],
    assignee: ["assignee", "A record task needs an assignee."],
  },
  log: {
    text: ["note", "A logged exchange needs a nonempty first line."],
    secondText: ["note", "Name who took part in the exchange."],
    note: ["note", "The note is too long for this record."],
    kind: ["activityType", "Choose a known kind."],
    day: ["dueDate", "The day falls in a closed period."],
    assignee: ["assignee", "A record task needs an assignee."],
  },
};

const documents = { console: operationDocuments };
const runtime = { icons: { ...baseIcons, ...projects.icons } };
/**
 * The models the pane's read and verbs name. The generic task verbs resolve them through the
 * schema's metadata, so completing or dropping a task refreshes the record's tasks only when it
 * names them.
 */
const resources = ["projects.Task", "projects.ActivityType", "messaging.Message"].map((model) => testDataResource(model));
const ASSIGNEE = { id: "usr_ada", username: "ada", display_name: "Ada Lovelace" };
const CATALOG: RecordTasksPayload["activity_types"] = [
  { id: "act_todo", key: "todo", name: "To do", glyph: "circle-check" },
  { id: "act_call", key: "call", name: "Call", glyph: "comments" },
];

/** The note the pane plans for, as a record page's aside hands it over. */
const RECORD: ChatterViewContext = {
  pathname: "/notes/nte_7",
  params: { id: "nte_7" },
  route: { name: "notes.record", path: "/notes/$id", viewType: "notes/note", modelLabel: "notes.Note", recordEdges: ["projects.Task"], recordParam: "id" },
  view: { kind: "record", type: "notes/note", sqid: "nte_7" },
};

/** The verb's answer when `refuse` refuses something of it, as the action guard answers a refusal: its message joins every reason. */
function refusal(verb: Verb, refuse: Refusal) {
  const reasons: Record<string, string[]> = {};
  for (const part of REFUSALS[refuse]) {
    const [key, reason] = SERVER_REFUSALS[verb][part];
    reasons[key] = [...(reasons[key] ?? []), reason];
  }
  if (Object.keys(reasons).length === 0) return null;
  return { ok: false, message: Object.values(reasons).flat().join("; "), validation_errors: reasons, id: null };
}

/** A string member of a verb's `input` as the transport resolved it; an absent or null one reads empty. */
function inputText(args: Record<string, unknown>, name: string): string {
  const value = typeof args.input === "object" && args.input !== null ? Reflect.get(args.input, name) : undefined;
  return typeof value === "string" ? value : "";
}

/** The server's order: open first, then by due date and title. */
function serverOrder(left: RecordTaskRow, right: RecordTaskRow): number {
  return Number(left.status !== "OPEN") - Number(right.status !== "OPEN")
    || (left.due_date ?? "").localeCompare(right.due_date ?? "")
    || left.title.localeCompare(right.title);
}

/**
 * The shipped pane for one note over an in-memory transport. The record's tasks and the kind catalog
 * are read from a list that a schedule, a log, a completion and a drop write to, as the server
 * defaults them: a schedule is due today and of the To do kind unless it says otherwise, a log is a
 * done task titled by its first line. `refuse` is read at each schedule or log, so changing it keeps
 * the list.
 */
export function RecordTasksStory({ appearance, refuse = "nothing", kinds = "listed", onRequest }: Partial<RecordTasksArgs> & {
  appearance?: RuntimeFixtureProps["appearance"];
  onRequest?: (request: GraphQLWireRequest) => void;
}) {
  const latestRefuse = useLatestRef(refuse);
  const schemas = React.useMemo(() => {
    let tasks: RecordTaskRow[] = [
      { id: "tsk_agreement", title: "Send the signed agreement", note: "Attach the countersigned copy.", status: "OPEN",
        due_date: "2026-03-05", done_at: null, activity_type: "todo", assignee: ASSIGNEE, task_actions: ["complete", "drop"] },
      { id: "tsk_kickoff", title: "Called about the kickoff date", note: "", status: "DONE",
        due_date: "2026-03-02", done_at: "2026-03-02T10:00:00Z", activity_type: "call", assignee: ASSIGNEE, task_actions: ["reopen"] },
    ];
    let created = 0;
    const add = (task: Omit<RecordTaskRow, "id" | "assignee">, message: string) => {
      const id = `tsk_${++created}`;
      tasks = [...tasks, { ...task, id, assignee: ASSIGNEE }];
      return { ok: true, message, validation_errors: null, id };
    };
    const change = (id: unknown, changes: Partial<RecordTaskRow>, message: string) => {
      tasks = tasks.map((task) => task.id === id ? { ...task, ...changes } : task);
      return { ok: true, message, validation_errors: null, id };
    };
    return storySchema({
      record_tasks: () => [...tasks].sort(serverOrder),
      activity_types: () => kinds === "listed" ? CATALOG : [],
      schedule_record_task: (args) => refusal("schedule", latestRefuse.current) ?? add({
        title: inputText(args, "title").trim(), note: inputText(args, "note"), status: "OPEN",
        due_date: inputText(args, "due_date") || formatDateStorage(new Date()), done_at: null,
        activity_type: inputText(args, "activity_type") || "todo", task_actions: ["complete", "drop"],
      }, "Task scheduled."),
      log_record_task: (args) => {
        const note = inputText(args, "note");
        return refusal("log", latestRefuse.current) ?? add({
          title: note.trim().split("\n")[0]!.trim(), note, status: "DONE", due_date: inputText(args, "occurred_on"),
          done_at: new Date().toISOString(), activity_type: inputText(args, "activity_type") || "todo", task_actions: ["reopen"],
        }, "Exchange logged.");
      },
      complete_task: ({ id }) => change(id, { status: "DONE", done_at: new Date().toISOString(), task_actions: ["reopen"] }, "Task completed."),
      drop_task: ({ id }) => change(id, { status: "DROPPED", task_actions: ["reopen"] }, "Task dropped."),
    }, { schema: "console", resources, onRequest });
  }, [kinds, latestRefuse, onRequest]);
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas} collectionPath="/notes"
    initialEntry="/notes/nte_7" resourceLabel="Notes" runtime={runtime} operationDocuments={documents}>
    <div className="mx-auto flex h-screen w-96 flex-col border-x border-border-subtle bg-sheet">
      <RecordTasksPane context={RECORD} />
    </div>
  </RoutedRuntimeFixture>;
}

export default {
  title: "Projects/Record tasks",
  parameters: { angeeOwnRuntime: true, layout: "fullscreen" },
  excludeStories: ["RecordTasksStory"],
  argTypes: {
    refuse: {
      control: { type: "select", labels: REFUSE_LABELS },
      options: Object.keys(REFUSALS),
      description: "What every schedule or log is refused for, under its server's keys: scheduling refuses the text as `title` "
        + "and the day as `dueDate`; logging takes the text and the note as one `note` and refuses the day as `dueDate`. "
        + "No field of the pane shows the assignee.",
    },
    kinds: {
      control: { type: "inline-radio", labels: { listed: "To do and Call", none: "None" } },
      options: ["listed", "none"],
      description: "Whether the kind catalog lists kinds, or none: the pane then draws no Type select.",
    },
  },
  args: { refuse: "nothing", kinds: "listed" } satisfies RecordTasksArgs,
  // The catalog is read once, with the record's tasks: another catalog starts the record again.
  render: (args: RecordTasksArgs, { globals }: { globals: Record<string, unknown> }) =>
    <RecordTasksStory key={args.kinds} appearance={storyAppearance(globals)} {...args} />,
};

type Story = { args?: Partial<RecordTasksArgs> };

/**
 * The Tasks pane of a note with one planned task, overdue, and one done. Type what needs doing and
 * press Schedule: the task shows under Planned, due today and of the To do kind unless a type and a
 * day were chosen, and the form clears. Switch to Log, type what happened, choose a type and the day
 * it happened and press Log: the exchange shows under Done. Tick a planned task to mark it done, or
 * hover it, press Cancel task and confirm to drop it. With `refuse` set, every schedule or log is refused:
 * each reason shows under the field it is about, which is marked invalid, and focus moves to the
 * first refused field; a reason about the assignee, which no field shows, shows on the banner below
 * the form, which then takes focus. With `kinds` set to None the pane draws no Type select.
 */
export const Default: Story = {};

/**
 * Every schedule or log is refused at every field the pane draws. Type what needs doing and press
 * Schedule: What needs doing, Notes, Type and Due date each show their reason under them and are
 * marked invalid, focus is on What needs doing, and no banner shows. Switch to Log, type what
 * happened, choose a type and the day and press Log: the log sends the text and the note as one
 * note, so both of their reasons show under What happened and Notes stays unmarked; Type and Day it
 * happened show theirs, and focus is on What happened. Set `kinds` to None: the reason about the
 * kind then shows on the banner, which takes focus.
 */
export const EveryFieldRefused: Story = { args: { refuse: "everyField" } };

/**
 * The catalog lists no kind, so the pane draws no Type select, and every schedule or log is refused
 * for its kind. Type what needs doing and press Schedule: with no field to show it, the reason shows
 * on the banner below the form as "activityType: Choose a known kind.", and focus is on the banner.
 * Set `refuse` to Nothing, switch to Log, type what happened, choose the day and press Log: it goes
 * out with no kind and the exchange shows under Done.
 */
export const EmptyCatalog: Story = { args: { kinds: "none", refuse: "kind" } };
