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
  "group.roster.actions": "Roster actions",
  "group.roster.add": "Add member",
  "group.roster.adding": "Adding…",
  "group.roster.addError": "Could not add the roster member.",
  "group.roster.changeRole": "Change role",
  "group.roster.saveRole": "Save role",
  "group.roster.savingRole": "Saving…",
  "group.roster.roleError": "Could not change the roster role.",
  "group.roster.remove": "Remove",
  "group.roster.removeTitle": "Remove roster member?",
  "group.roster.removeDescription": "This party will lose its role in this space.",
  "group.roster.removeError": "Could not remove the roster member.",
  "group.roster.role.owner": "Owner",
  "group.roster.role.moderator": "Moderator",
  "group.roster.role.member": "Member",
  "group.threads.title": "Conversation",
  "group.threads.messages": "Messages",
  "group.threads.empty": "No group conversations in this space yet.",
};

export const useSpacesT = createNamespaceT("spaces", enSpacesMessages);
