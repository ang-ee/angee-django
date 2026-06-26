// Re-export shim. The login/OAuth-callback auth surface (LoginPage,
// UsernamePasswordForm, OAuthCallback, safeRedirectPath + the login slots) moved
// up into `@angee/app` — it is an app-shell page concern the host mounts as a
// route, and its only consumers are addon web + the host, never a package below.
// This preserves the `@angee/base` auth import surface for those consumers.
export * from "@angee/app/auth";
