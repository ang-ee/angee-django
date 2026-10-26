import { createNamespaceT } from "@angee/ui";
export const enBackupMessages = {
  "savePlan": "Save plan", "saveAndBackup": "Save & back up", "cancel": "Cancel", "reviewFields": "Review the plan fields before continuing.", "captureFailed": "The backup could not be started. Review the reported error and retry.",
  "unsupportedSelection": "This selection contains unsupported resources. Resolve the reported reasons or exclude those resources before backing up. You can still save this plan.",
  "retryDiscovery": "Retry discovery",
  "previewStale": "Updating discovery for the current selection…",
  "loadingDestination": "Loading destination…", "destinationUnavailable": "Destination files are unavailable or you do not have access.",
  "includeChecked": "Include checked", "excludeChecked": "Exclude checked",
  "plans": "Backup plans", "backups": "Backups", "name": "Name", "target": "Target stack", "destination": "Destination",
  "selection": "Selection", "planEmpty": "No backup plans yet.", "backupEmpty": "No completed backups yet.",
  "captureContents": "Every archive includes stack configuration and recovery secrets. Keep downloaded archives private.",
  "selectionHint": "Discover the stack to review this selection before saving or backing up. Exclusions apply to both All discovered and Selected.",
  "databases": "Databases", "service_state": "Services", "workspaces": "Workspaces", "sources": "Source repositories", "storage": "Storage",
  "mode": "Include", "all": "All discovered", "selected": "Selected", "none": "None", "names": "Selected resource names", "exclude": "Excluded resource names", "resourceName": "Resource name",
  "policy": "Source capture", "bundle": "Bundle source contents", "fetchPublished": "Fetch published pinned revisions", "sourceHint": "Bundle preserves local and unpublished source contents. Fetch published requires proof that each pinned revision is available from its remote; unknown or local-only revisions are reported and never silently omitted.",
  "invalidSelection": "This plan has an unsupported selection schema. Correct it before running a backup.",
  "discover": "Discover & estimate", "discovering": "Discovering…", "preview": "Discovery", "previewHint": "Discovery reviews the current draft. Changes are saved only when you accept this review.",
  "previewEmpty": "Discover the stack to review included, excluded, unsupported, and unavailable resources.", "group": "Group", "resource": "Resource", "status": "Status", "reason": "Reason", "sourceBytes": "Estimated source bytes", "knownSourceBytes": "Known source bytes", "unknown": "Unknown", "unknownSizes": "Some source sizes are unknown. The total includes known source bytes only.", "compressionHint": "Source sizes are estimates before capture. ZIP compression and final archive size are not estimated.", "discoveredAt": "Discovered", "run": "Back up now", "runConfirm": "Capture this saved plan now?", "runDescription": "The workflow captures an immutable selection and reports progress in its run record.",
  "plan": "Backup plan", "backup": "Backup", "execution": "Workflow run", "archive": "Archive", "created": "Created", "manifest": "Capture manifest", "download": "Download", "loadingArchive": "Loading archive…", "downloadUnavailable": "Archive is unavailable or you do not have access to its file.", "size": "Archive size", "checksum": "Checksum", "localTarget": "This deployment currently supports its local stack and the operator-managed backup destination.", "captures": "Completed backups"
};
export const useBackupT = createNamespaceT("operator_backup", enBackupMessages);
