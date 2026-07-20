import { createNamespaceT } from "@angee/ui";

export const enStorageIntegrateMessages: Record<string, string> = {
  "mount.connect.button": "Connect local folder",
  "mount.connect.title": "Connect local folder",
  "mount.connect.description":
    "Make a folder on this Django host available as a storage drive.",
  "mount.connect.name": "Name",
  "mount.connect.namePlaceholder": "Shared documents",
  "mount.connect.mode": "File handling",
  "mount.connect.modeCopy": "Copy files",
  "mount.connect.modeReference": "Leave files in place",
  "mount.connect.submit": "Connect",
  "mount.connect.submitting": "Connecting",
  "mount.connect.cancel": "Cancel",
  "mount.connect.error": "Could not connect the local folder.",
  "mount.browse.currentFolder": "Current folder",
  "mount.browse.up": "Up",
  "mount.browse.useThisFolder": "Use this folder",
  "mount.browse.loading": "Loading folders",
  "mount.browse.empty": "This location has no child folders.",
  "mount.browse.alreadyMounted": "Already mounted",
  "mount.browse.notReadable": "Not readable",
  "mount.browse.truncated":
    "Only the first 1,000 locations are shown. Enter a token to reach another location.",
  "mount.browse.manualHint": "Enter or paste a source location",
  "mount.browse.error": "Could not browse the mount source.",
  "mount.name": "Name",
  "mount.group.sync": "Sync",
  "mount.action.sync": "Sync now",
};

export const useStorageIntegrateT = createNamespaceT(
  "storage",
  enStorageIntegrateMessages,
);
