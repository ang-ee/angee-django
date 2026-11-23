---
"@angee/app": minor
---

`useSignInRedirect(redirectTo?)` (`@angee/app/auth`) returns the navigation
after a sign-in (the page's safe `next`, else the safe `redirectTo`, else `/`,
as a full page load), which `LoginPage` uses and a custom sign-in route can
share. `safeRedirectPath` resolves a `next` value the way the browser will and
accepts only a same-origin result, returning its resolved path, query and
fragment, so values such as `/<tab>/host` no longer escape to another site.
Browser error reports leave URL fragments out of the page address, navigation
and request breadcrumbs, and stack frame locations.
