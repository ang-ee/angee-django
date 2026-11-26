---
"@angee/ui": patch
---

The toast region is named in the app's language. `ToastProvider` passed no
label to the toast library's viewport, so the region a screen reader announces
and a person reaches by landmark was always the library's English default,
"Notifications". It now reads the `ui` namespace's new `toast.region` key
(English: "Notifications"), which a host's translations answer like every other
key.
