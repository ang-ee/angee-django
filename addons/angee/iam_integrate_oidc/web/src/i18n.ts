import { createNamespaceT } from "@angee/ui";

export const enOidcMessages: Record<string, string> = {
  // OAuth login methods (the public sign-in slot).
  "login.loadingOptions": "Loading sign-in options…",
  "login.providersUnavailable": "Sign-in providers unavailable",
  "login.passwordStillAvailable":
    "Username and password sign-in is still available.",
  "login.continueWith": "Continue with {provider}",
  "login.startFailed": "Sign-in could not start",
  "login.startError": "Could not start sign-in.",
  "login.providerFallback": "provider",

  // OAuth callback page.
  "callback.completing": "Completing sign-in…",
  "callback.confirming": "Your session is being confirmed.",
  "callback.signInFailed": "Could not sign in",
  "callback.backToSignIn": "Back to sign in",
  "callback.completeError": "Could not complete sign-in.",
  "callback.browserOnly":
    "The sign-in callback can only be completed in a browser.",
  "callback.missingInfo":
    "The sign-in callback is missing required information.",

  // The OIDC section contributed into the OAuth client form.
  "oidc.section": "Sign-in (OIDC)",
};

export const useOidcT = createNamespaceT("iam-integrate-oidc", enOidcMessages);
