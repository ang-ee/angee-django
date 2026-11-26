---
"@angee/refine": minor
"@angee/ui": patch
---

A record action the server refuses tells the person why. When a verb's target is
refused before the verb runs (not found, not allowed), the server answers with the
verb's summary as the message ("Confirm failed.") and the reason in-band, in
`validation_errors`. `runActionResult`, which projects an action's outcome onto
the rendered action's contract, threw only the message, so the action bar's toast
read "Confirm / Confirm failed." and the reason was lost. It now throws the
reasons, and the message when the refusal carries none. Every record action
projected through `runActionResult` now shows the in-band reason without an
edit. No toast title changes. Other refusals (a transition not allowed, a
missing object) still read the verb's summary: the server sends no reason for
them.

`@angee/refine` exports `actionOutcomeReasons(outcome)`: a refusal's in-band
reasons, under any key, each once, as one line. `useActionResultRun` composes it
instead of its own copy of the rule, and so does a refused row of a bulk trash
or restore, which showed only the first reason.

Upgrade: code that matched the thrown error's text against the outcome's
`message` finds the reasons there when the refusal carries any.
