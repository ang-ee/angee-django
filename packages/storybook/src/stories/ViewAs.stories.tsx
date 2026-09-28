import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { AppRuntimeProvider, ViewAsBanner, ViewAsPicker } from "@angee/ui";

const meta = { title: "Chrome/ViewAs", component: ViewAsBanner } satisfies Meta<typeof ViewAsBanner>;
export default meta;

const realUser = { id: "manager-1", name: "Alex" };
const people = [{ id: "responder-1", name: "Morgan" }, { id: "requester-1", name: "Sam" }];

function Preview() {
  const [userId, setUserId] = useState<string | null>("responder-1");
  const currentUser = people.find((person) => person.id === userId) ?? realUser;
  return <AppRuntimeProvider runtime={{ auth: {
    user: currentUser,
    status: "authenticated",
    hasRole: () => false,
    viewAs: {
    viewAs: userId ? { userId } : null,
    realUser,
    currentUser,
    viewablePeople: people,
    enter: setUserId,
    exit: () => setUserId(null),
  } } }}>
    <div className="grid max-w-3xl gap-4">
      <ViewAsBanner />
      <ViewAsPicker />
    </div>
  </AppRuntimeProvider>;
}

export const InjectedIdentity: StoryObj<typeof meta> = { render: () => <Preview /> };
