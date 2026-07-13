import { createNamespaceT } from "@angee/ui";

export const enNexusMessages: Record<string, string> = {
  "ties.party": "Contact",
  "ties.gravity": "Gravity",
  "ties.messages": "Messages",
  "ties.lastContact": "Last contact",
  "ties.fading": "Fading",
  "ties.touchDue": "Touch due",
  "ties.cadence": "Stay in touch (days)",
  "ties.group.cadence": "Stay in touch",
  "timeline.tab": "Timeline",
  "timeline.count": "{count} messages across every channel",
  "timeline.loadOlder": "Load older",
  "timeline.empty": "No messages exchanged with this contact yet.",
  "timeline.inbound": "Inbound",
  "timeline.outbound": "Outbound",
  "timeline.internal": "Internal",
};

export const useNexusT = createNamespaceT("nexus", enNexusMessages);
