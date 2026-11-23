---
"@angee/app": minor
"@angee/ui": patch
---

An `ANGEE_UI.shell.apps` entry may omit `rail`: such an app shows every menu
root with its own brand and theme, else the shell's, so `apps: {console: {}}`
makes `?app=console` the full console on any host (such an entry may not set
`home`; the boot check refuses one). The new `ANGEE_UI.shell.app` names the app
a page opens when neither `?app=` nor the hostname's `hosts` entry selects one,
and is checked at boot like `hosts`. A `RuntimeSelection` (`@angee/ui/runtime`)
may therefore name an `app` while its `rail` is `null`, which the developer
summary shows as "(every root)".
