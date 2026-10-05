---
"@angee/app": minor
"@angee/ui": minor
---

Loading the next page: hovering, focusing or touching an in-app link preloads
its route (`defaultPreload: "intent"`), so a code-split page is usually ready on
click. While one still loads, the previous page's portaled control band and
statusline hide with the page instead of staying visible and clickable, and the
router's fallback is a page skeleton (an empty control band row over placeholder
rows) instead of a centred spinner.

- `@angee/ui`: `useInAppLink(href, handlers, options)` replaces
  `useInAppLinkClick` and returns the anchor's click and intent handlers;
  `InAppLinkProvider` takes an optional `preload` (`routerPreloader(router)`),
  which nested providers inherit; `hrefLocation(router, href)` converts an
  in-app href to router location options. `LoadingPanel` takes
  `shape="page"`.
- `@angee/app`: `?debug` applies from the router's `onBeforeLoad` event instead
  of the root route's `beforeLoad`; the `/` redirect carries location options,
  so a preload of `/` follows it to the home page.
