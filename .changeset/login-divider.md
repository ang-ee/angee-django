---
"@angee/app": patch
---

`LoginPage` shows the "or use password" divider only beside a sign-in method that
renders. A contributed method that renders nothing, such as a single sign-on
method with no configured connection, no longer leaves an orphan divider above
the password form.
