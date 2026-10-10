import type { ReactElement } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { AppRuntimeProvider } from "@angee/ui";
import { baseIcons, defaultWidgets, type Tone, type WidgetField } from "@angee/ui";

// A run-state field declares the dot display once; stopped/running/error/warning
// then resolve to grey/green/red/amber through the shared status vocabulary.
const runtimeOptions = [
  { value: "STOPPED", label: "Stopped" },
  { value: "RUNNING", label: "Running" },
  { value: "ERROR", label: "Error" },
  { value: "WARNING", label: "Warning" },
];

// A product field can use the same display while owning its vocabulary locally.
const taskOptions = [
  { value: "BLOCKED", label: "Blocked" },
  { value: "READY", label: "Ready for next stage" },
  { value: "IN_PROGRESS", label: "In progress" },
  { value: "DONE", label: "Done" },
];

const taskTone: Record<string, Tone> = {
  BLOCKED: "danger",
  READY: "success",
  IN_PROGRESS: "warning",
  DONE: "neutral",
};

const meta = {
  title: "Widgets/Status Display",
  parameters: { layout: "padded" },
} satisfies Meta;

export default meta;

type Story = StoryObj<typeof meta>;

const Read = defaultWidgets.statusBadge.read;

function Palette({
  title,
  options,
  tone,
}: {
  title: string;
  options: WidgetField["options"];
  tone?: Record<string, Tone>;
}): ReactElement {
  return (
    <section className="space-y-2">
      <h3 className="text-2xs font-semibold uppercase text-fg-muted">{title}</h3>
      <div className="flex flex-col items-start gap-1.5">
        {(options ?? []).map((option) => (
          <Read
            key={option.value}
            value={option.value}
            field={{ options, statusDisplay: "dot", tone }}
            readOnly
          />
        ))}
      </div>
    </section>
  );
}

export const Palettes: Story = {
  render: () => (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>
      <div className="flex gap-16">
        <Palette title="Runtime status (shared vocabulary)" options={runtimeOptions} />
        <Palette title="Task stage (explicit tone map)" options={taskOptions} tone={taskTone} />
      </div>
    </AppRuntimeProvider>
  ),
};
