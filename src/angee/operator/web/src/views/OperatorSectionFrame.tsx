import type * as React from "react";

import { OperatorTransportProvider } from "../data/transport";

export interface OperatorSectionFrameProps {
  children: React.ReactNode;
}

/**
 * Per-route operator console frame: opens the daemon transport for the section
 * body. Section navigation lives in the chrome's menu (the "Operator" dropdown),
 * not a tab bar here.
 */
export function OperatorSectionFrame({
  children,
}: OperatorSectionFrameProps): React.ReactNode {
  return <OperatorTransportProvider>{children}</OperatorTransportProvider>;
}
