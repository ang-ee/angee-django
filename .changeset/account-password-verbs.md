---
"@angee/app": minor
"@angee/ui": minor
"@angee/refine": minor
---

The signed-in user carries `hasUsablePassword` (`AuthUser` in `@angee/app`,
`RuntimeAuthUser` in `@angee/ui`), so a user-menu item can hide a password
action for a session without one. The identity query reads it from the user
type's new `has_usable_password` field, so upgrade the Angee backend with the
packages. A dialog opened from a contributed user-menu item stays mounted and
returns focus to the menu's trigger when it closes. A form field may declare
`autoComplete`, which the password and textarea widgets pass to their input.
`useActionMutation`, `useActionResultMutation` and
`useRecordChromeActionMutation` accept `errorNotification: false` and
`transient`, for a verb carrying a secret input: its mutation state is cleared
once it settles, and a second call while one runs rejects.
