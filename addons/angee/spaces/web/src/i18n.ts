import { createNamespaceT } from "@angee/ui";

export const enSpacesMessages: Record<string, string> = {
  "group.parent": "Parent space",
  "group.details": "Details",
  "group.visibility": "Visibility",
  "group.makePublic": "Make public",
  "group.makePrivate": "Make private",
  "group.tabs.roster": "Roster",
  "group.tabs.threads": "Threads",
  "group.roster.party": "Party",
  "group.roster.role": "Role",
  "group.roster.empty": "No roster members in this space yet.",
  "group.threads.title": "Conversation",
  "group.threads.messages": "Messages",
  "group.threads.empty": "No group conversations in this space yet.",
};

export const useSpacesT = createNamespaceT("spaces", enSpacesMessages);
