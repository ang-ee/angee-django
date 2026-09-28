import type { Meta, StoryObj } from "@storybook/react-vite";
import { LoginPage } from "@angee/app/auth";
import { AppRuntimeProvider } from "@angee/ui";

const meta = { title: "Layouts/Login", component: LoginPage,
  parameters: { layout: "fullscreen", route: "/login" } } satisfies Meta<typeof LoginPage>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
export const Branded: StoryObj<typeof meta> = {
  render: () => <AppRuntimeProvider runtime={{ brand: { name: "Workspace", mark: "folder" } }}>
    <LoginPage methods={null} />
  </AppRuntimeProvider>,
};
