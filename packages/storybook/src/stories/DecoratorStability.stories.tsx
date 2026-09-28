import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Input } from "@angee/ui";

function StatefulPreview({ label }: { label: string }) {
  const [value, setValue] = useState("");
  const [count, setCount] = useState(0);
  return <div className="grid max-w-sm gap-3">
    <p>Change the theme, color scheme or label control; the draft and count should remain.</p>
    <Input aria-label="Draft" value={value} onChange={(event) => setValue(event.target.value)} />
    <Button onClick={() => setCount((current) => current + 1)}>{label}: {count}</Button>
  </div>;
}

const meta = { title: "Foundations/Decorator stability", component: StatefulPreview,
  args: { label: "Count" } } satisfies Meta<typeof StatefulPreview>;
export default meta;
export const Draft: StoryObj<typeof meta> = {};
