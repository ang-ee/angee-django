import { createNamespaceT } from "@angee/ui";

export const enPlatformIntegrateOperatorMessages: Record<string, string> = {
  "restart.running.title": "Restarting application",
  "restart.running.waiting": "Waiting for the operator…",
  "restart.failed.title": "Application restart failed",
  "restart.failed.fallback": "The operator could not complete the restart.",
  "restart.startFailed.title": "Could not start application restart",
  "restart.statusUnavailable.title": "Restart status unavailable",
  "restart.statusUnavailable.description": "Could not verify whether addon changes require a restart.",
  "restart.required.title": "Application restart required",
  "restart.required.description": "Addon changes are saved and will take effect after the application restarts.",
  "restart.action": "Restart application",
  "restart.retry": "Retry",
  "restart.logs": "View logs",
};

export const usePlatformIntegrateOperatorT = createNamespaceT(
  "platformIntegrateOperator",
  enPlatformIntegrateOperatorMessages,
);
