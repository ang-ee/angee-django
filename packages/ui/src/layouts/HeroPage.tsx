import * as React from "react";

import { cn } from "../lib/cn";
import { Page, PageBody } from "../page";

export interface HeroPageProps {
  align?: "center" | "top";
  chrome?: boolean;
  className?: string;
  children?: React.ReactNode;
  maxWidth?: "sm" | "md" | "lg" | "xl";
}

const heroPageMaxWidth: Record<NonNullable<HeroPageProps["maxWidth"]>, string> = {
  sm: "max-w-md",
  md: "max-w-2xl",
  lg: "max-w-4xl",
  xl: "max-w-6xl",
};

export function HeroPage({
  align = "center",
  chrome = true,
  className,
  children,
  maxWidth = "md",
}: HeroPageProps): React.ReactElement {
  const content = (
    <div
      className={cn(
        "flex min-h-full w-full flex-col items-center px-6 py-12",
        align === "center" ? "justify-center" : "justify-start",
        chrome ? undefined : className,
      )}
    >
      <div className={cn("w-full", heroPageMaxWidth[maxWidth])}>{children}</div>
    </div>
  );

  if (!chrome) return content;

  return (
    <Page className={cn("min-h-full", className)}>
      <PageBody gutter="none" scroll="auto">
        {content}
      </PageBody>
    </Page>
  );
}
