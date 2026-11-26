# Emailed sign-in links

A person can sign in without a password through an emailed link that works once.
This addon adds the link to IAM's account verbs: `User.issue_sign_in_link()`,
contributed by the `UserSignInLinks` donor, extends `User.account_verbs` and
`User.account_action_blockers` through `super()`, so the users list offers
**Send sign-in link** (`issue_sign_in_link` in `account_actions`) exactly where
the verb sends one. A host without this addon offers no sign-in link.

## Issuing a link

The verb runs under IAM's `lock_for_account_verb`: it locks the row and checks
the `issue_sign_in_link` permission, which this addon's fragment declares on
`auth/user` (platform admins) and unions into IAM's `active_account_granted`
and `nonstaff_account_granted`. It refuses what IAM's verbs refuse (protected,
service, the actor's own account), inactive accounts, any account without a
valid email and a deployment that cannot deliver the link. It also refuses a
second link within `ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN` seconds (60 by
default). It stores a `SignInLink` row with a keyed digest of the token, never
the token itself, and a digest of the account facts the link is bound to. The
link leaves through Django's default mailer once the issuing transaction
commits, as plain text to the account's `email`. Where that mailer records mail,
the link is stored with it, and "accepted" means recorded. The row then records
`sent_at`. The action never shows the link, and `created_by` records who issued
it. System callers issue under `system_context`, which leaves `created_by`
empty; an automatic invitation must follow an approval a person reviewed, never
an unreviewed request. The console mutation is `issue_user_sign_in_link` (an
`ActionResult`).

Only the newest link the mailer accepted signs in. Issuing again retires the
older ones once the new email is sent, and a failed send leaves the previous
link working. A link expires after `ANGEE_IAM_MESSAGING_SIGN_IN_LINK_TTL`
seconds (three days by default). Like Django's password-reset tokens, it is
bound to the account's email, password and last sign-in: a new address, a
password set or changed, or any sign-in retires it. Saving the account inactive
(`set_active` or an administrative update) retires it for good, so reactivation
revives none. It never signs in a protected account. Rotating `SECRET_KEY` voids
every outstanding link, because `salted_hmac` ignores `SECRET_KEY_FALLBACKS`.
Whoever may change an account's email (`write__email`, administrators by
default) can then send its links to the new address. Nothing notifies the
previous address.

## Redeeming a link

Redemption is Django's `authenticate()` with the credentials of
`angee.iam_messaging.auth.sign_in_credentials`, through `SignInLinkBackend`,
which this addon appends to `AUTHENTICATION_BACKENDS` after IAM's axes and
`ModelBackend` entries, so django-axes throttles links as it does passwords.
Django masks the token (`sign_in_token`) before `user_login_failed` receivers
see it; the account public id it names travels unmasked as IAM's
credential-neutral `sign_in_account`, under which `axes_username` records the
attempt. The token's account public id is not a secret: an unknown one refuses
like a wrong secret. A request makes one sign-in attempt: `login` and
`redeem_sign_in_link` share `angee.iam.auth.authenticate_once`. A genuine link
that is spent (used, expired, replaced or retired, or for an account that can no
longer sign in) is marked through IAM's `mark_spent_credential`, so IAM's
`spent_credential` whitelist exempts that one attempt: its holder had the real
link and made no guess. The public mutation `redeem_sign_in_link(token)` signs
the person in and returns them; every refusal is the same `ok: false`. The
session records IAM's `SESSION_BACKEND` (`angee.iam.auth.ModelBackend`), so it
outlives this addon's backend being removed or renamed.

Links open `ANGEE_PUBLIC_ORIGIN` + `/sign-in-link#token=...`. Base owns that
setting and its rule (`angee.base.origins.public_origin`): only an `https`
origin (`http` only under `DEBUG`) with a valid host and port and no wildcard,
user info or path builds links, and `angee.E051` reports any other set value.
Links are also built only when `DEFAULT_FROM_EMAIL` is a valid address and
`MAILERS["default"]` exists. Otherwise no link is offered and the verb refuses.
The token rides the fragment, which no browser request, access log or referrer
carries, and Angee's browser error reporting strips fragments from what it
records. This addon's public `/sign-in-link` route renders IAM's login page with
the link as its sign-in method. It clears the token from the address bar
through the router and redeems it only when the person clicks, so a mail scanner
that opens the link does not spend it. After sign-in it shows who is now signed
in before continuing. The password form stays below it as the fallback.

## Where the token goes

The token still leaves the server inside the email. The provider's message
store, a database-backed mail queue and webmail link wrappers can keep it, and
so does a click-tracking redirect. The browser's history, and any history sync,
can record the visited URL, fragment included, before the page clears it. Click
tracking is set per provider account or message stream, not per message, so
send these emails through a stream with tracking off. Until the commit sends it,
the token also lives in the on-commit callback (`partial(link.send, token)`).
An error reporter that records frame locals would see it there. Angee's Sentry
setup records none, and the functions holding the token are marked
`sensitive_variables` for Django's own reports. One-time passwords remain the
path for an account that cannot receive email.

## Permissions

Platform admins send links. The permission is this addon's own declaration on
IAM's `auth/user`; IAM's own Zed names no link verb. The fragment's arms land in
IAM's union points, so IAM's sealed disclosures (`angee.iam.E004`) keep the
verb to active, non-staff accounts.
