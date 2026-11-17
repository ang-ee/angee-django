import { createNamespaceT } from "@angee/ui/i18n";

export const enStorageIntegrateMessages: Record<string, string> = {
  "mount.localFolder.button": "Connect local folder",
  "mount.localFolder.title": "Connect local folder",
  "mount.localFolder.description":
    "Make a folder on this Django host available as a storage drive.",
  "mount.localFolder.namePlaceholder": "Shared documents",
  "mount.localFolder.error": "Could not connect the local folder.",
  "mount.connect.name": "Name",
  "mount.connect.account": "Account",
  "mount.connect.sourceFolder": "Source folder",
  "mount.connect.mode": "File handling",
  "mount.connect.modeCopy": "Copy files",
  "mount.connect.modeReference": "Leave files in place",
  "mount.connect.copyBackend": "Copy into",
  "mount.connect.copyBackendDefault": "Default storage",
  "mount.account.use": "Use this account",
  "mount.account.loading": "Loading accounts",
  "mount.account.empty": "No connected account can read this source yet.",
  "mount.account.connect": "Connect another account",
  "mount.account.error": "Could not load your accounts.",
  "mount.browse.chooseAccount": "Choose an account to browse its folders.",
  "mount.connect.submit": "Connect",
  "mount.connect.submitting": "Connecting…",
  "mount.connect.menu": "Connect",
  "mount.browse.up": "Up",
  "mount.browse.open": "Open folder",
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
  "storage-integrate",
  enStorageIntegrateMessages,
);
