import type { ReactNode } from "react";

import { OperatorSectionFrame } from "./OperatorSectionFrame";
import { GitOpsSection } from "./sections/GitOpsSection";
import { OperationsSection } from "./sections/OperationsSection";
import { OverviewSection } from "./sections/OverviewSection";
import { SecretsSection } from "./sections/SecretsSection";
import { ServicesSection } from "./sections/ServicesSection";
import { SourcesSection } from "./sections/SourcesSection";
import { TemplatesSection } from "./sections/TemplatesSection";
import { WorkspacesSection } from "./sections/WorkspacesSection";

/** Wrap a section in the operator console frame (tab nav + daemon transport). */
function framed(Section: () => ReactNode): () => ReactNode {
  return function OperatorPage(): ReactNode {
    return (
      <OperatorSectionFrame>
        <Section />
      </OperatorSectionFrame>
    );
  };
}

export const OperatorOverviewPage = framed(OverviewSection);
export const OperatorServicesPage = framed(ServicesSection);
export const OperatorWorkspacesPage = framed(WorkspacesSection);
export const OperatorSourcesPage = framed(SourcesSection);
export const OperatorGitOpsPage = framed(GitOpsSection);
export const OperatorOperationsPage = framed(OperationsSection);
export const OperatorTemplatesPage = framed(TemplatesSection);
export const OperatorSecretsPage = framed(SecretsSection);
