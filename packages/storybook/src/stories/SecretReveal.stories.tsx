import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, ModalsHost, useSecretReveal } from "@angee/ui";

function RevealDemo(): React.ReactElement {
  const reveal = useSecretReveal();
  return (
    <Button variant="primary" onClick={() => void reveal({
      title: "Sign-in details",
      body: "Copy these sign-in details before closing. They will not be shown again.",
      secrets: [
        { name: "username", label: "Username", value: "ada" },
        { name: "password", label: "Password", value: "7f3a-once-only-9c1d" },
      ],
      copy: { label: "Copy sign-in details", value: "Username: ada\nPassword: 7f3a-once-only-9c1d" },
    })}>
      Reveal sign-in details
    </Button>
  );
}

const meta = {
  title: "Feedback/SecretReveal",
  component: RevealDemo,
  parameters: { layout: "centered" },
  decorators: [(Story) => <ModalsHost><Story /></ModalsHost>],
} satisfies Meta<typeof RevealDemo>;

export default meta;

type Story = StoryObj<typeof meta>;

/** The one-time reveal: read-only copyable fields plus one combined copy control. */
export const OneTimeReveal: Story = {};
