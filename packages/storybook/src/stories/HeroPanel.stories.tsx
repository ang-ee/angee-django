import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  BrandLockup,
  Button,
  Glyph,
  HeroPanel,
  MetricStrip,
} from "@angee/base";

const meta = {
  title: "Fragments/HeroPanel",
  component: HeroPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof HeroPanel>;

export default meta;

type Story = StoryObj;

export const Feature: Story = {
  render: () => (
    <HeroPanel
      actions={
        <>
          <Button variant="primary">
            <Glyph name="plus" />
            New workspace
          </Button>
          <Button variant="secondary">View docs</Button>
        </>
      }
      body="Use framework primitives for the host shell, then let addons own the product contracts."
      brand={<BrandLockup label="Angee" mark={<Glyph name="angee" />} size="sm" />}
      className="max-w-5xl"
      commandStrip={
        <MetricStrip
          metrics={[
            { label: "Addons", value: "8", detail: "5 active" },
            { label: "Schemas", value: "3", detail: "All current" },
            { label: "Jobs", value: "12", detail: "2 running" },
            { label: "Users", value: "46", detail: "10 online" },
          ]}
        />
      }
      headline="Compose addons into one deterministic surface."
    />
  ),
};

