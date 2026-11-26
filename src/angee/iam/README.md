# IAM access and credential actions

The Share dialog is IAM's one-record People surface. `record_readers` lists
named effective person readers through REBAC's subject lookup after the
model's declared share permissions are checked. Wildcard and authenticated
audiences have no finite person roster.
The lookup returns identities, not the relation paths that granted them. Exact
direct grants come from `record_access`, including groups, subject types and
each grantable relation; a reader may have more than one. A model owner
declares a component as a `<model>#access-roles` child (IAM's model-scoped
`iam#access-roles` container). It registers its live
role roster and admission/removal verbs with `useAccessRole`; IAM merges those
rows only with the effective readers returned by that lookup. A role
admission that requests a follow grants read and follows in one server
transaction. `ShareAccessRailGroup`, as a `<model>#rail` child, presents the
same Share adapter in the record rail. Direct relation labels come from scoped
resource vocabulary's `relations` map; an undeclared label displays the
lowercase relation id. A visibility policy offers only its owner-declared
values and states their consequence alongside the control. A model owner
registers one through a `<model>#access-visibility` child and
`useAccessVisibility`; proposals uses it for the round's opening policy and its
confirmed opening verb.

## The people directory

- **Names for everyone signed in.** `auth/user#read` is `authenticated`, so a
  record embedding an account (assignee, owner, requester, `run_as`) shows its display name to every reader. The web draws the
  avatar's initials from that name. Records that carry a label instead resolve
  the same IAM label elevated (`angee.iam.identity`).
- **Restricted fields** are field gates on `read_private` (`self + list`):
  username, email, last sign-in, staff and active state, and UI preferences.
  The person and people managers read them; for anyone else they are null.
  `display_name` stays IAM's label: the full name, or, for an account with no
  name, its sign-in name, on every surface.
- **The full list is for people managers.** `auth/user#list` admits platform
  admins, the named readers of the whole directory (`iam/directory:main#reader`)
  or of one account (`directory_reader`), and the roles a consumer unions in.
  `UserManager.directory` and the `users` resource's `directory` filter list
  their rows; the managed people list applies that filter, so it is empty,
  without an error, for anyone who manages no one. Neither reader relation
  accepts a wildcard, and the demo resources seed no directory reader.
- **Pickers offer colleagues.** `auth/user#colleague` is the person's own
  account and every account for people managers. Parties adds the active people
  whose identity the member reads (thread followers, task requesters). A
  relation declared on `auth/user` makes every write to its backing rows need
  `write` on the account, so team and space rosters, whose moderators edit
  memberships, contribute no arm.
  `UserManager.colleagues` scopes the `users` resource and the `colleagues`
  query. Their search and ordering use the sign-in name and email only where
  the viewer reads them.
- **Query axes.** Gated columns are never filters, sorts or groups. The `users`
  resource filters activity through its `active` expression, REBAC's
  `readable("is_active")`, which is NULL wherever the viewer cannot read the
  field. The `iam.users.active` and `iam.users.deactivated`
  presets use it.

## Managed people

Saved User records inherit IAM's account verbs as `iam.User#actions-menu`
children: deactivate/reactivate (`set_user_active`), rename (`rename_user`,
name fields only), give access (`issue_user_password`), reset access
(`reset_user_password`), set a chosen password (`set_user_password`) and send a
sign-in link (`issue_user_sign_in_link`, below). Set password confirms the new
password locally; the other changing verbs take an explicit confirmation.
Changing verbs take the revision the client read (the issue verbs excepted),
check their own Zed permission on `auth/user`
(`set_active`, `rename`, `issue_password`, `reset_password`,
`issue_sign_in_link`, `set_password`; admin by default)
and refuse protected accounts: the actor's own, staff, superusers and effective
members of `iam/protected:main#member`, except Set password's administrative
admission described below. Issue and reset return the username with
a one-time secret, shown only in the shared `useSecretReveal` prompt with a copy
control; nothing reaches a record or query cache. Controls read the row's
`account_actions` projection, which batches the verbs' own conditions.

The generic update runs through `write` plus `write__<field>` gates: identity,
credential and authority columns, a person's UI preferences and the account's
audit dates (`date_joined`, `last_login`) stay with administrators, and a
protected account also needs `administer`. People save their own preferences
through the self-service mutation, and IAM stamps `last_login` on sign-in in
place of Django's receiver. `last_login` reads through `read__last_login`.
An app mounts the managed people list on its own route
lazily, with `lazyRouteComponent(() => import("@angee/iam/users"), "UsersPage")`.
The users list renders `iam.users#columns` (username, email, staff, active,
last sign-in) and ships the `iam.users.active` and `iam.users.deactivated`
presets. A consumer makes its manager role a people manager, grants it these
powers and marks the role as elevated from its own fragment, as
[`tests/extcontrib`](../../../tests/extcontrib/permissions.extends.zed) does:

```zed
definition auth/user {
    relation manager: consumer/role // rebac:const=manager
    permission list = manager->effective_member
    permission write = manager->effective_member
    permission set_active = manager->effective_member
    permission rename = manager->effective_member
    permission issue_password = manager->effective_member
    permission reset_password = manager->effective_member
}
definition iam/protected {
    relation manager: consumer/role // rebac:const=manager
    permission member = manager->effective_member
}
```

## Chosen passwords

The user menu's **Change password** form calls `change_password` in the same
session schema as preferences. `User.change_password` acts only on its actor's
own account and authenticates its current password through `authenticate_once`
before locking the row: the login throttle and one-attempt-per-request rule
apply. It rejects an unchanged password. The mutation has no user id.
Django's `update_session_auth_hash` keeps the current session signed in and
rotates its key; other sessions lose their valid password hash. Link-only and
OIDC-only accounts keep their sign-in method. The session user's boolean
`has_usable_password` hides the menu entry when explicitly false; an identity
payload without that fact leaves the entry visible. The server still refuses
accounts without a usable password. Under view-as the server returns null for
this fact, the menu omits Change password, and the preview rejects the mutation.

The user record's **Set password** action submits a chosen password through
`set_user_password(id, password, expected_revision)`, returning `ActionResult`.
`User.set_account_password` checks `set_password`, the shared account blockers
and the required observed revision before validation or hashing. Zed owns the
permission's `admin->member & write & write__password` intersection; IAM seals
it so fragments cannot bypass platform-admin membership or either write gate
(`angee.iam.E003`). Extending the protected-update permission `administer` does
not widen this audience. A concrete platform-administrator user can set another
person's password across **all protected classes**: peer administrators, staff,
superusers and holders of any elevated role, as well as ordinary accounts.
The actor's own account still requires Change password with the current password,
which keeps the requesting session; service and other non-person accounts remain
refused. Issue password, Reset password and the other account verbs retain their
protected-account refusals. A successful set invalidates the target's existing
session hashes and retires outstanding sign-in links. After commit, IAM logs one
`set_password` event with `actor_public_id` and `target_public_id`; it records no
password, hash or validator message. Actorless system calls retain the full
protected-account refusal; other non-user subjects cannot set passwords.
**Contract change:** the raw `users` update input no longer accepts `password`.
Set a password with `set_user_password`; creation still accepts `password`.
Metadata updates and password changes are separate operations.

`User._set_validated_password` runs Django's configured validators with the user
as context, then hashes the value. Actor-requested `create_person` (including
GraphQL creation), Set password and Change password all use it. Trusted
`create_user`/`create_superuser` bootstrap, resource and OIDC paths keep Django's
native factory behavior; `create_person_as_system` creates passwordless people.

Both forms use masked inputs with explicit autocomplete and transient mutation
handling. Both confirm the new password locally; confirmation never reaches the server.
The new mutations return no password, and neither the form's record nor the
database stores a plaintext password.

## Sign-in links

A person can sign in without a password through an emailed link that works once.
`User.issue_sign_in_link()` is an account verb like `issue_password`: it locks
the row, checks the `issue_sign_in_link` permission (admin by default, unioned
into `active_account` and `nonstaff_account`) and refuses protected, inactive
and service accounts and any account without a valid email. It also refuses a
second link within `ANGEE_IAM_SIGN_IN_LINK_COOLDOWN` seconds (60 by default).
It stores a `SignInLink` row with a keyed digest of the token, never the token
itself, and a digest of the account facts the link is bound to. Once that row
commits, the link goes to the account's `email` through Django's default mailer
as plain text; where that mailer records mail, the link is stored with it. The
row then records `sent_at`, and `sign_in_link_sent` fires with the account and
the row. `created_by` records who issued it. System callers issue under
`system_context`, which leaves `created_by` empty; an automatic invitation must follow an approval a person
reviewed, never an unreviewed request. The console mutation is
`issue_user_sign_in_link` (an `ActionResult`), offered through `account_actions`
as `issue_sign_in_link`.

Only the newest link the mailer accepted signs in. Issuing again retires the
older ones once the new email is sent, and a failed send leaves the previous
link working. A link expires after `ANGEE_IAM_SIGN_IN_LINK_TTL` seconds (three
days by default). Like Django's password-reset tokens, it is bound to the
account's email, password and last sign-in: a new address, a password set or
changed, or any sign-in retires it. Saving the account inactive (`set_active`
or an administrative update) retires it for good, so reactivation revives none. It never signs in a protected account.
Rotating `SECRET_KEY` voids every outstanding link, because `salted_hmac`
ignores `SECRET_KEY_FALLBACKS`. Whoever may change an account's email
(`write__email`, administrators by default) can then send its links to the new
address. Nothing notifies the previous address.

Redemption is Django's `authenticate()` with the credentials of
`angee.iam.auth.sign_in_credentials`, through `SignInLinkBackend`, so
django-axes throttles links as it does passwords. Django masks the token before
`user_login_failed` receivers see it. The token's account public id is not a
secret: an unknown one refuses like a wrong secret. A request makes one sign-in
attempt: `login` and `redeem_sign_in_link` share `angee.iam.auth.authenticate_once`,
so a second attempt in the same HTTP request (aliased or batched fields) is
refused with "Only one sign-in attempt is allowed per request." before
`authenticate()` runs.

IAM owns the axes policy in its autoconfig. `AXES_COOLOFF_TIME` lifts a lockout
after an hour, and nothing else does: a successful sign-in clears no failures,
so a client cannot reset its count by signing in to an account it holds. Axes
records each failure under the client's IP address and a username, which
`AXES_USERNAME_CALLABLE` sets. For a link, the username is the account public id
the token names; for an account that does not exist, it is the string the token
carries. For a password, it is the sign-in name. IAM leaves axes locking out by
IP address. A deployment that adds `username` to `AXES_LOCKOUT_PARAMETERS`
throttles each account under those keys, separately for links and for
passwords, and never under an empty username. `AXES_WHITELIST_CALLABLE` exempts
a genuine link that is spent (used, expired, replaced or retired, or for an
account that can no longer sign in): its holder had the real link and made no
guess. The exemption covers only that attempt and only credentials naming the
same account, and it ends with the attempt. A project that sets its own
whitelist callable loses that exemption. The public mutation
`redeem_sign_in_link(token)` signs the person in and returns them; every refusal
is the same `ok: false`.

Links open `ANGEE_PUBLIC_ORIGIN` + `/sign-in-link#token=...`. Base owns that
setting and its rule (`angee.base.origins.public_origin`): only an `https`
origin (`http` only under `DEBUG`) with a valid host and port and no wildcard,
user info or path joins the CSRF trusted origins and builds links, and
`angee.E051` reports any other set value. Links are also built only when
`DEFAULT_FROM_EMAIL` is a valid address and `MAILERS["default"]` exists;
otherwise no link is offered and the verb refuses. Where messaging is installed,
a default mailer always exists (messaging's system mailer, which records mail),
so this no longer says the mail can leave: a host with no transport mailer
offers a link whose send raises and stamps nothing. And "accepted" then means
recorded: a link whose delivery later fails is still stamped as sent and
replaces the link that worked. The token rides the
fragment, which no browser request, access log or referrer carries, and
Angee's browser error reporting strips fragments from what it records. IAM's
public `/sign-in-link` route renders the login page, with the link as its
sign-in method. It clears the token from the address bar through the router
and redeems it only when the person clicks, so a mail scanner that opens the
link does not spend it. After sign-in it shows who is now signed in before
continuing. The password form stays below it as the fallback.

The token still leaves the server inside the email. The provider's message
store, a database-backed mail queue and webmail link wrappers can keep it, and
so does a click-tracking redirect. The browser's history, and any history sync,
can record the visited URL, fragment included, before the page clears it. Click tracking is set per provider account
or message stream, not per message, so send these emails through a stream with
tracking off. Until the commit sends it, the token also lives in the on-commit
callback (`partial(link.send, token)`). An error reporter that records frame
locals would see it there. Angee's Sentry setup records none, and the functions
holding the token are marked `sensitive_variables` for Django's own reports.
One-time passwords remain the path for an account that cannot receive email.

## Presence

`current_user { permitted(refs: [String!]!) }` answers which
`<app_label.ModelName>#<permission>` refs the identity (the viewed one in a
preview) holds at type level: the engine's create-path evaluation of a
candidate row with no proposed relationships, batched, so role- and
const-backed arms and the administrator set decide and a row-dependent arm is
false. An unknown model or permission is an error; anonymous sessions have no
identity and hold none. The web shell asks once per identity load for every
`requires` its menus and container children declare and leaves out what the
identity lacks; the server still decides data, row verbs and field values. The
users list's last sign-in column (`iam.last-login`) requires
`iam.User#read__last_login`, so it shows to people managers; its `self` arm is
row-dependent and holds at no type level. The users menu entry requires
`iam.User#list`.
