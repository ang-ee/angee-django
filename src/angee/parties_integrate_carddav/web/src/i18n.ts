import { createNamespaceT } from "@angee/ui";

export const enCardDavMessages = {
  "directory.connect.button": "CardDAV account",
  "directory.connect.title": "Connect a CardDAV account",
  "directory.connect.description":
    "Enter your CardDAV server URL and Basic-auth credentials. Each address book has a stream of enrolled contacts.",
  "directory.connect.name": "Name",
  "directory.connect.namePlaceholder": "Personal address book",
  "directory.connect.serverUrl": "Server URL",
  "directory.connect.serverUrlPlaceholder": "https://dav.example.com/",
  "directory.connect.username": "Username",
  "directory.connect.password": "Password",
  "directory.connect.submit": "Connect",
  "directory.connect.submitting": "Connecting…",
  "directory.connect.error": "Could not connect the directory.",
};
export const useCardDavT = createNamespaceT("parties_integrate_carddav", enCardDavMessages);
