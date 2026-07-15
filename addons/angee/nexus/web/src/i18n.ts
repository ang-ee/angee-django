import { createNamespaceT } from "@angee/ui";

export const enNexusMessages: Record<string, string> = {
  "ties.partyA": "Party A",
  "ties.partyB": "Party B",
  "ties.aToB": "A → B",
  "ties.bToA": "B → A",
  "ties.gravity": "Gravity",
  "ties.messages": "Messages",
  "ties.lastContact": "Last contact",
  "ties.fading": "Fading",
  "ties.group.pair": "Pair",
  "ties.group.analytics": "Analytics",
  "cadences.party": "Party",
  "cadences.days": "Stay in touch (days)",
  "cadences.touchDue": "Touch due",
  "cadences.group.schedule": "Schedule",
  "timeline.tab": "Timeline",
  "timeline.count": "{count} messages across every channel",
  "timeline.loadOlder": "Load older",
  "timeline.empty": "No messages exchanged with this contact yet.",
  "timeline.inbound": "Inbound",
  "timeline.outbound": "Outbound",
  "timeline.internal": "Internal",
};

export const useNexusT = createNamespaceT("nexus", enNexusMessages);
