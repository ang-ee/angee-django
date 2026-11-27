# Emailed sign-in links

A person can sign in without a password through an emailed link that works once.
This addon adds the link to IAM's account actions: `User.issue_sign_in_link()`,
contributed by the `UserSignInLinks` donor, extends `User.account_actions` and
`User.account_action_blockers` through `super()`, so the users list offers
**Send sign-in link** (`issue_sign_in_link` in `account_actions`) exactly where
the action sends one. A host without this addon offers no sign-in link. The
link's mail is a message template this addon ships, sent as messaging's system
mail in the recipient's languages, so the addon requires messaging, the message
templates and parties, whose person holds those languages.

## Issuing a link

The action runs under IAM's `lock_for_account_action`: it locks the row and checks
the `issue_sign_in_link` permission, which this addon's fragment declares on
`auth/user` (platform admins) and unions into IAM's `active_account_granted`
and `nonstaff_account_granted`. It refuses what IAM's actions refuse (protected,
service, the actor's own account), inactive accounts, any account whose email
system mail cannot be sent to (messaging's `Message.objects.clean_system_recipient`:
one address the handle owner lands, which the transport can carry; the refusal
names no address) and a deployment that cannot deliver the link. It also refuses a
second link within `ANGEE_IAM_MESSAGING_SIGN_IN_LINK_COOLDOWN` seconds (60 by
default). The checks are in two halves: the permission, the deliverability and
the address in the action (`UserSignInLinks.issue_sign_in_link`, on the locked
row), then the cooldown, the template, the render and the mail in
`SignInLinkManager.issue`. Then, in the action's transaction, `issue`
mails the link ([The mail](#the-mail)) and stores a `SignInLink` row with a
keyed digest of the token, never the token itself, a digest of the account
facts the link is bound to, and `sent_at`, the moment its mail was recorded and
queued, set on every link `issue` saves. Nothing is written before the last
refusal: a refused issue records no link and no mail. The action never shows
the link, and `created_by` records who issued it. System callers issue under
`system_context`, which leaves `created_by` empty; an automatic invitation must
follow an approval a person reviewed, never an unreviewed request. The console
mutation is `issue_user_sign_in_link` (an `ActionResult`).

Only the account's newest link signs in: issuing again retires the older ones as soon
as the new link's mail is recorded, and a refused issue leaves the previous
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

## The mail

The mail is the message template `iam_messaging.sign_in_link`
(`SIGN_IN_LINK_TEMPLATE`), which this addon ships in its `install` resources in
English and Spanish, in the template library's `base` layout with its greeting,
button, ignore notice and signature. The row a person reads is picked for them
by `MessageTemplate.objects.effective("template", ..., person=...)`: their
person's languages, then the deployment's default. A deployment edits the text
as an override of the shipped row, as for any message template
([message templates](../messaging_templates/README.md)), and the override must
show `link` in both bodies: the send asks it (`must_print=["link"]`), so an
override that drops the link, from either body or from a block it includes, or
shows it only under a condition that is false for this person, is refused when a
link is issued. The action refuses in band ("The sign-in link's email template does
not show the link…", code `link_not_shown`), nothing is written, the earlier link
stays current, and a warning on this addon's logger names the template row and
the refusal's code. Any other refusal of the render (a value the template reads
as a date, a value or a text over its limit, a render that fails) is answered the
same way in this addon's words ("…could not be rendered for this mail…", code
`link_not_rendered`), never in the render's, which can quote a value, the link
among them. The system channel's and compose's refusals pass as they are. The
template reads
three values, which the server builds: `link`, the single-use link; `expires_at`,
when it stops working, printed in the deployment's time zone; and
`person.first_name`, the account's first name, the one value a person typed,
which the renderer keeps text. A recipient reads, in English: "Hi Ada," (or
"Hi there,"), that the link signs them in once and when it expires, that a
newer link replaces it, the **Sign in** button (in the plain text, `Sign in:`
and the link), the ignore notice and the brand's signature; the subject is "Your
sign-in link".

The link is system mail ([messaging's System mail](../messaging/README.md#system-mail)):
`MessageTemplate.objects.send_as_system` renders the row as named system work
under the reason `iam_messaging.sign_in_link.send` and records it on messaging's
system channel, queued, in the action's transaction, with the administrator who
asked as the cause. Messaging delivers it through the transport mailer once that
transaction commits; Django's default mailer is not on its path. The account
stores its address lower case; the handle owner lands it on the existing handle
of that address in any letter case, and the mail carries that handle's spelling.

A link is offered only where it can be built and its mail sent, read from
settings alone (`SignInLink.deliverable()`): the deployment's public origin
(below) and system mail configured, as `Channel.objects.system_mail_configured()`
answers ([messaging's System mail](../messaging/README.md#system-mail)).
Otherwise no link is offered, and the action refuses without naming a setting:
an operator sets `ANGEE_PUBLIC_ORIGIN` and messaging's transport and sender. What
settings cannot tell is refused in band, with nothing written: the system
channel's row missing (`SYSTEM_CHANNEL_MISSING`, under `channel`) or the template
missing (`template_missing`), which mean provision's resource load has not run,
and the system channel paused by an administrator
(`OUTBOUND_EMAIL_CHANNEL_MISSING`, under `channel`).

`sent_at` means recorded and queued, not delivered, and the new link is current
from then on, in the same transaction as its mail. The action says no more than
that ("Sign-in link queued for the account's email."): the mail's delivery is the
recorded message's to show, on the system channel, where administrators read it
as sent, failed or uncertain; the person sees nothing until a mail arrives.
Sending again issues a new link, which retires the one before it, so the link of
a mail that failed, or that was uncertain and did arrive, no longer signs in.
Two consequences follow:

- a link whose delivery later fails still has its `sent_at`, and it replaces the
  link that worked, so the person holds no working link until an administrator
  sends another;
- a delivery whose connection fails is retried on the system channel with the
  same mail, but one that drops while the mail is being sent stays uncertain
  and is not retried, since it may have left: only an administrator can resolve
  it, or send another link.

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

Links open the deployment's public origin + `/sign-in-link#token=...`. Base owns
that origin, `ANGEE_PUBLIC_ORIGIN`, and its one accessor
(`angee.base.origins.configured_public_origin`): only an `https` origin (`http`
only under `DEBUG`) with a valid host and port and no wildcard, user info or path
builds links, and `angee.E051` reports any other set value.
The token rides the fragment, which no browser request, access log or referrer
carries, and Angee's browser error reporting strips fragments from what it
records. This addon's public `/sign-in-link` route renders IAM's login page with
the link as its sign-in method. It clears the token from the address bar
through the router and redeems it only when the person clicks, so a mail scanner
that opens the link does not spend it. After sign-in it shows who is now signed
in before continuing. The password form stays below it as the fallback.

## Where the link is stored

The token is minted in `SignInLinkManager.issue` and leaves it only inside the
mail; nothing here logs it, puts it in a refusal or holds it in an on-commit
callback. A host that logs `django.db.backends` at `DEBUG` logs every
statement's parameters, so it logs each link the send writes. Angee's Sentry
setup sends no frame's local variables (`include_local_variables=False`), so its
reports never hold it. With `DEBUG` off, Django's error reports, an HTML admin
error mail (an `AdminEmailHandler` with `include_html`) included, show none
either. Under `DEBUG`, Django filters nothing, and its debug page shows every
local. Each entry of this addon below GraphQL that holds the token or the link
hides all its locals with a bare `@sensitive_variables()`
(`SignInLinkManager.issue` and `redeem`, `SignInLinkBackend.authenticate`);
Django's filter cleanses every frame below a mark the same way, the helpers
they call (`SignInLink.url_for`, the token's parts and digests) among them;
and the template's send and messaging's write and delivery mark their
own. So, with `DEBUG` off, a failure before the render, in its refusal, inside
the write, in delivery or in a redemption shows the token in no frame. An
unexpected failure inside either GraphQL mutation reaches no Django report:
Angee's schema logs its class, its path and its frames as text (file, line and
source line, with no locals and no exception value), then detaches the exception,
so Strawberry's own error line, which follows, carries no traceback either, and
no log record holds one a report could be built from. That detaching, not a
mark, is what protects the token on the mutations' path: a traceback from there
would show it in GraphQL's own frames above the resolver, which no mark
reaches. Strawberry's line quotes the request's document where the error
points, so a token passed as a variable, as this addon's page passes it, is
logged nowhere, but one written into the document itself is logged there.
Django never filters an exception's own message: nothing this addon raises
carries the link or the address, and no refusal of the render passes in the
render's own words, so no answer, banner or log line carries the secret. What
remains is a database's own error text. A transport's never leaves the delivery
task: when recording a failed delivery fails too, messaging raises that failure
alone and logs the transport's error by its types. Django's plain-text error
report shows no local variables. The mail is stored on the
system channel, where the link lives until it signs in, expires or is retired:

- the message's text and HTML parts, and the content-addressed text fragments
  they point to, which are indexed for search and kept after the message is
  deleted;
- the message's preview, the first 280 characters of its text, which hold the
  link whole unless the text before it is long (a long first name pushes some
  or all of it out).

Deleting the message does not revoke the link; retiring it does (a newer link,
deactivation, a new email address or password, any sign-in), as does its expiry.
Who reads the system channel is messaging's ([System mail](../messaging/README.md#system-mail));
through every verb those are administrators, the people who hold this action. A
reader can reach no account beyond what Issue password or Reset password
already reaches: the same protected and inactive accounts are refused. Unlike
those two actions, which replace the person's password, end their sessions and
are logged against the administrator, a stored link lets a reader sign in as
the person once, and the person notices nothing until their own link reads as
spent. The link's row keeps who issued it and when it was used.

The token still leaves the server inside the email. The provider's message
store and webmail link wrappers can keep it, and so does a click-tracking
redirect. The browser's history, and any history sync, can record the visited
URL, fragment included, before the page clears it. Click tracking is set per
provider account or message stream, not per message, so send these emails
through a stream with tracking off. One-time passwords remain the path for an
account that cannot receive email.

## Permissions

Platform administrators send links, and for now no other role can be given the
action. The permission is this addon's own declaration on IAM's `auth/user`;
IAM's own Zed names no link action. The permission merge lets other fragments
add arms only to the permissions a resource type's owner declares, so a
consumer's `permission issue_sign_in_link = …` is refused ("already declared by
a contributor"), unlike its arm on IAM's `issue_password`. A role that should
send links waits for that merge to change.

The fragment also unions the action into IAM's `active_account_granted` and
`nonstaff_account_granted`, so the action's holders see which accounts are
active and which are not staff, as offering it needs. The blockers keep the
action to those accounts: IAM's person-kind and protected-account ones and this
addon's inactive and undeliverable ones.
