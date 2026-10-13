import type { ServiceState } from "./types";

type ObservedService = Pick<ServiceState, "status" | "outcome">;

/**
 * The daemon's verdict on a service: its `outcome`, the vocabulary every runtime
 * shares (running, completed, failed, restarting, stopped, skipped, pending,
 * never-run, unknown). `status` is the runtime's own word and stands in only when
 * the daemon did not read the runtime, as in a create or update result.
 */
export function serviceVerdict(service: ObservedService): string {
  return (service.outcome || service.status).toLowerCase();
}

/** Whether the runtime is running the service, or restarting it after an exit. */
export function serviceActive(service: ObservedService): boolean {
  return ["running", "restarting"].includes(serviceVerdict(service));
}
