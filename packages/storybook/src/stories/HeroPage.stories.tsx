import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Card, FieldRow, FormGrid, HeroPage, Input } from "@angee/ui";

const meta = {
  title: "Layouts/HeroPage",
  component: HeroPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof HeroPage>;

export default meta;

type Story = StoryObj<typeof meta>;

export const CenteredCard: Story = {
  render: () => (
    <div className="h-screen bg-inset">
      <HeroPage maxWidth="sm">
        <Card>
          <div className="space-y-5 p-6">
            <header className="space-y-1">
              <h1 className="text-lg font-semibold text-fg">Sign in</h1>
              <p className="text-13 text-fg-muted">
                Use your workspace credentials to continue.
              </p>
            </header>
            <FormGrid columns="one">
              <FieldRow label="Email" required>
                <Input type="email" placeholder="you@example.com" />
              </FieldRow>
              <FieldRow label="Password" required>
                <Input type="password" placeholder="••••••••" />
              </FieldRow>
            </FormGrid>
            <Button variant="primary" className="w-full">
              Continue
            </Button>
          </div>
        </Card>
      </HeroPage>
    </div>
  ),
};
