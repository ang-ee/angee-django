import type { Meta, StoryObj } from "@storybook/react-vite";
import { LOGIN_CONTAINERS, LoginPage } from "@angee/app/auth";
import { AppRuntimeProvider, containersFromChildren } from "@angee/ui";

const meta = { title: "Layouts/Login", component: LoginPage,
  parameters: { layout: "fullscreen", route: "/login" } } satisfies Meta<typeof LoginPage>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
export const Branded: StoryObj<typeof meta> = {
  render: () => <AppRuntimeProvider runtime={{ brand: { name: "Workspace", mark: "folder" } }}>
    <LoginPage methods={null} />
  </AppRuntimeProvider>,
};

/** A branding fragment's one `auth.login#hero` child, full-bleed: it shows at every width, above the card on phones. */
function WorkspaceHero() {
  return <section className="flex h-full flex-col justify-end gap-3 text-n-0">
    <p className="text-12 font-semibold uppercase tracking-[0.18em] text-n-0/72">Workspace</p>
    <h1 className="max-w-[14ch] text-4xl font-semibold leading-tight">Everything your team signs in for.</h1>
  </section>;
}

const brandedHero = containersFromChildren(LOGIN_CONTAINERS, {
  "auth.login#hero": { "workspace.login-hero": { content: { render: WorkspaceHero, presentation: "full-bleed" } } },
});

export const BrandedHero: StoryObj<typeof meta> = {
  render: () => <AppRuntimeProvider runtime={{ brand: { name: "Workspace", mark: "folder" }, containers: brandedHero }}>
    <LoginPage methods={null} />
  </AppRuntimeProvider>,
};
