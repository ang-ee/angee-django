import type { Meta, StoryObj } from "@storybook/react-vite";
import { useMemo, useState } from "react";
import {
  ActionTrigger, AppRuntimeProvider, Button, Chip, CommandBand, FORM_CONTAINERS, Glyph, ModalsHost,
  SaveDiscardActions, containersFromChildren, useAppRuntime, type RecordChromeContext, type RecordNavigation,
} from "@angee/ui";

/**
 * The record page's command band: `⋮` menu · lifecycle verbs · (space) · the
 * record's `form#chrome` controls · pager. FormView renders this band for every
 * form record; explorer subjects (a file, a page, a message) render it directly.
 */
const meta = {
  title: "Page/CommandBand",
  component: CommandBand,
  parameters: { layout: "padded" },
} satisfies Meta<typeof CommandBand>;

export default meta;

type Story = StoryObj;

const record = { id: "bill_9311", number: "BILL/2026/00006", state: "DRAFT", permissions: ["read", "write"] };
const chrome: RecordChromeContext = {
  resource: "accounting.Invoice", canonicalResource: "accounting.Invoice", dataProviderName: "public",
  recordId: record.id, record, formReadOnly: false,
};
const noop = () => undefined;

// The `form#chrome` controls come from addon contributions (Needs you, Follow,
// Share); the story contributes one so the band shows where they sit.
const chromeContainers = containersFromChildren(FORM_CONTAINERS, {
  "form#chrome": { "story.follow": { content: <Button size="iconSm" variant="ghost" aria-label="Follow" pressed><Glyph name="bell" /></Button> } },
});

function Band({ dirty = false, trashed = false }: { dirty?: boolean; trashed?: boolean }) {
  const runtime = useAppRuntime();
  const fixtureRuntime = useMemo(() => ({ ...runtime, containers: chromeContainers }), [runtime]);
  const [position, setPosition] = useState(2);
  const navigation: RecordNavigation = {
    current: position, total: 6,
    onPrev: () => setPosition((value) => Math.max(1, value - 1)),
    onNext: () => setPosition((value) => Math.min(6, value + 1)),
  };
  return (
    <ModalsHost>
      <AppRuntimeProvider runtime={fixtureRuntime}>
        <CommandBand
          blocked={dirty}
          menu={<>
            <ActionTrigger glyph="copy" onClick={noop}>Copy link</ActionTrigger>
            <ActionTrigger glyph="file" onClick={noop}>Duplicate</ActionTrigger>
            <ActionTrigger glyph="trash" variant="danger" onClick={noop}>Move to trash</ActionTrigger>
          </>}
          verbs={dirty ? (
            <SaveDiscardActions isDirty onDiscard={noop} onSave={noop} />
          ) : trashed ? (
            <Button size="sm" variant="secondary"><Glyph name="restore" />Restore</Button>
          ) : (
            <>
              <Button size="sm" variant="primary"><Glyph name="check" />Post</Button>
              <Button size="sm" variant="danger"><Glyph name="x" />Cancel bill</Button>
            </>
          )}
          chrome={chrome}
          end={dirty ? <Chip tone="warning">Unsaved changes</Chip> : undefined}
          navigation={navigation}
        />
      </AppRuntimeProvider>
    </ModalsHost>
  );
}

/** Menu, two lifecycle verbs, the record's chrome controls and the pager over the navigator's order. */
export const Default: Story = { render: () => <Band /> };

/** The dirty form: Save and Discard replace the lifecycle verbs, the menu is blocked, the end slot carries the save status. */
export const DirtyForm: Story = { render: () => <Band dirty /> };

/** A trashed record offers Restore alone. */
export const Trashed: Story = { render: () => <Band trashed /> };
