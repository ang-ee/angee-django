// English fallback strings for the emailed sign-in link addon. The host runtime
// owns the active translations; these are the defaults used when a key is
// missing. Components resolve them through `useIamMessagingT()` (below), and the
// manifest contributes this bundle as the `iam_messaging` namespace.

import { createNamespaceT } from "@angee/ui/i18n";

export const enIamMessagingMessages: Record<string, string> = {
  // The account action on a saved user record.
  "users.sendSignInLink": "Send sign-in link",
  "users.sendSignInLink.confirmTitle": "Send a sign-in link?",
  "users.sendSignInLink.confirmBody": "This emails the account a link that signs them in once and expires. Any earlier link stops working. The link is never shown here.",

  // Sign-in link page (public).
  "signInLink.continue": "Sign in with your link",
  "signInLink.refusedTitle": "This sign-in link cannot be used",
  "signInLink.refused": "It may be used, expired or replaced by a newer one. Open the latest link from your email, ask for a new one, or sign in with your password.",
  "signInLink.unreachableTitle": "Could not reach the server",
  "signInLink.unreachable": "Your link may still work. Check your connection and try again.",
  "signInLink.retry": "Try again",
  "signInLink.signedInAs": "You are signed in as {name}.",
  "signInLink.proceed": "Continue",
};

// A translator bound to the `iam_messaging` namespace, falling back to the bundled English.
export const useIamMessagingT = createNamespaceT("iam_messaging", enIamMessagingMessages);
