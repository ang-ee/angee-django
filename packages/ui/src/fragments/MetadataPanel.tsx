import * as React from "react";

import { cn } from "../lib/cn";
import { tv } from "../lib/variants";
import { Card, CardContent, CardHeader } from "../ui/card";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { InfoRow } from "./InfoRow";
import { MetaGrid, MetaSection, type MetaGridProps } from "./MetaGrid";

export interface MetadataSection {
  children?: React.ReactNode;
  id?: string;
  rows?: MetaGridProps["rows"];
  title: React.ReactNode;
}

export interface MetadataTab {
  content: React.ReactNode;
  id: string;
  label: React.ReactNode;
}

export type MetadataPanelProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "title"
> & {
  badges?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  lead?: React.ReactNode;
  sections?: readonly MetadataSection[];
  subtitle?: React.ReactNode;
  tabs?: readonly MetadataTab[];
  title?: React.ReactNode;
};

export const metadataPanelVariants = tv({
  slots: {
    root: "min-w-0 overflow-hidden shadow-none",
    header: "border-b border-border-subtle pb-4",
    headerRow: "flex min-w-0 items-start gap-3",
    titleWrap: "min-w-0 flex-1",
    title: "break-words text-lg font-semibold leading-tight text-fg",
    subtitle: "mt-1 break-all font-mono text-2xs text-fg-muted",
    badges: "mt-3 flex flex-wrap gap-1",
    content: "space-y-5",
    tabStack: "space-y-2",
  },
});

export const MetadataPanel = React.forwardRef<HTMLElement, MetadataPanelProps>(
  function MetadataPanel(
    {
      badges,
      children,
      className,
      lead,
      sections,
      subtitle,
      tabs,
      title,
      ...props
    },
    ref,
  ) {
    const styles = metadataPanelVariants();
    const hasHeader =
      title != null || subtitle != null || lead != null || badges != null;

    return (
      <Card
        ref={ref}
        className={styles.root({ className })}
        density="md"
        {...props}
      >
        {hasHeader ? (
          <CardHeader className={styles.header()}>
            <div className={styles.headerRow()}>
              {lead}
              <div className={styles.titleWrap()}>
                {title != null ? (
                  <h2 className={styles.title()}>{title}</h2>
                ) : null}
                {subtitle != null ? (
                  <p className={styles.subtitle()}>{subtitle}</p>
                ) : null}
              </div>
            </div>
            {badges != null ? <div className={styles.badges()}>{badges}</div> : null}
          </CardHeader>
        ) : null}

        <CardContent className={cn(styles.content(), !hasHeader && "pt-4")}>
          {sections?.map((section, index) => (
            <MetaSection key={section.id ?? index} title={section.title}>
              {section.rows ? <MetaGrid rows={section.rows} /> : section.children}
            </MetaSection>
          ))}

          {tabs && tabs.length > 0 ? (
            <section className={styles.tabStack()}>
              <SectionEyebrow as="h3">Details</SectionEyebrow>
              <dl className="space-y-2">
                {tabs.map((tab) => (
                  <InfoRow
                    key={tab.id}
                    className="rounded-md bg-inset px-3 py-2"
                    label={tab.label}
                    value={tab.content}
                  />
                ))}
              </dl>
            </section>
          ) : null}

          {children}
        </CardContent>
      </Card>
    );
  },
);
MetadataPanel.displayName = "MetadataPanel";
