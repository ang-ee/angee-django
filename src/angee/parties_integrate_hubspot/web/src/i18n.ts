import { createNamespaceT } from "@angee/ui/i18n";

export const enHubSpotMessages = {
  "directory.connect.button": "HubSpot account",
  "directory.connect.title": "Connect a HubSpot account",
  "directory.connect.description":
    "Paste a HubSpot service key with the crm.objects.contacts.read scope. HubSpot checks the key before anything is saved, and its contacts sync one way into one address book.",
  "directory.connect.name": "Name",
  "directory.connect.namePlaceholder": "Sales contacts",
  "directory.connect.token": "Service key",
  "directory.connect.submit": "Connect",
  "directory.connect.submitting": "Connecting…",
  "directory.connect.error": "Could not connect the directory.",
};
export const useHubSpotT = createNamespaceT("parties_integrate_hubspot", enHubSpotMessages);
