import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, FormRoot, NumberField, RadioGroup } from "@angee/ui";

const meta = {
  title: "Primitives/Form",
  component: FormRoot,
  parameters: {
    layout: "centered",
  },
  argTypes: {
    density: {
      control: "select",
      options: ["compact", "comfortable", "spacious"],
    },
    layout: {
      control: "select",
      options: ["stack", "inline", "panel", "plain"],
    },
    validationMode: {
      control: "select",
      options: ["onSubmit", "onBlur", "onChange"],
    },
  },
  args: {
    density: "comfortable",
    layout: "stack",
    validationMode: "onSubmit",
  },
} satisfies Meta<typeof FormRoot>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: (args) => (
    <FormRoot {...args} className="w-[420px] p-4">
      <FormRoot.Field
        name="title"
        label="Title"
        required
        validate={(value) =>
          typeof value === "string" && value.trim().length > 0
            ? null
            : "Enter a title"
        }
      >
        <FormRoot.Control required placeholder="Q3 operating review" />
      </FormRoot.Field>
      <FormRoot.Field
        name="limit"
        label="Seat limit"
        description="Use a value from 1 to 250."
      >
        <NumberField min={1} max={250} defaultValue={25} />
      </FormRoot.Field>
      <div className="flex justify-end gap-2">
        <Button type="reset" variant="ghost" size="sm">
          Reset
        </Button>
        <Button type="submit" variant="primary" size="sm">
          Save
        </Button>
      </div>
    </FormRoot>
  ),
};

export const ExternalErrors: Story = {
  render: () => (
    <FormRoot
      className="w-[420px] p-4"
      errors={{
        title: "A project with this title already exists.",
        cadence: "Choose a notification cadence.",
      }}
    >
      <FormRoot.Field name="title" label="Title">
        <FormRoot.Control defaultValue="Launch plan" />
      </FormRoot.Field>
      <FormRoot.Field name="cadence" label="Cadence">
        <RadioGroup name="cadence">
          <RadioGroup.Item value="daily" label="Daily" />
          <RadioGroup.Item value="weekly" label="Weekly" />
          <RadioGroup.Item value="paused" label="Paused" />
        </RadioGroup>
      </FormRoot.Field>
      <div className="flex justify-end">
        <Button type="submit" variant="primary" size="sm">
          Submit
        </Button>
      </div>
    </FormRoot>
  ),
};

export const Panel: Story = {
  render: () => (
    <FormRoot layout="panel" density="compact" className="w-[420px] p-4">
      <FormRoot.Field name="email" label="Email" description="Used for alerts.">
        <FormRoot.Control type="email" defaultValue="sofia@example.com" required />
      </FormRoot.Field>
      <FormRoot.Field name="threshold" label="Alert threshold">
        <NumberField defaultValue={80} min={0} max={100} />
      </FormRoot.Field>
    </FormRoot>
  ),
};
