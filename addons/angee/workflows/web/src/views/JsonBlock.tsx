import * as React from "react";
import { CodeBlock } from "@angee/ui";

export function JsonBlock({ value }: { value: unknown }): React.ReactElement {
  return (
    <CodeBlock wrap className="max-h-56">
      {JSON.stringify(value ?? {}, null, 2)}
    </CodeBlock>
  );
}
