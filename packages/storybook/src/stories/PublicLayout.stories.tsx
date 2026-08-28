import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Glyph, Input, Label, PublicLayout } from "@angee/ui";

const meta = {
  title: "Layout/PublicLayout",
  component: PublicLayout,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof PublicLayout>;

export default meta;

type Story = StoryObj;

export const SignIn: Story = {
  render: () => (
    <div className="-m-6 min-h-screen">
      <PublicLayout
        brand={
          <div className="flex items-center gap-3 text-n-0">
            <span className="grid size-9 place-content-center rounded-6 bg-n-0/10">
              <Glyph name="angee" />
            </span>
            <span className="text-2xl font-bold">Angee</span>
          </div>
        }
        hero={
          <section className="flex min-h-screen flex-col justify-end px-8 pb-12 text-n-0">
            <div className="max-w-xl">
              <div className="text-34 font-semibold leading-tight">
                Deterministic application composition.
              </div>
              <p className="mt-3 text-sm leading-6 text-n-0/75">
                A public layout surface for login and account recovery flows.
              </p>
            </div>
          </section>
        }
        footer="Angee framework preview"
      >
        <form className="grid gap-4">
          <div>
            <Label htmlFor="email">Email</Label>
            <Input id="email" value="ada@example.com" readOnly />
          </div>
          <div>
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" value="password" readOnly />
          </div>
          <Button type="button" variant="primary" className="w-full">
            Sign in
          </Button>
        </form>
      </PublicLayout>
    </div>
  ),
};
