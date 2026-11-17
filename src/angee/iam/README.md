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
(`reset_user_password`) and send a sign-in link (`issue_user_sign_in_link`,
below). Each takes a confirmation and the revision the client
read (the issue verbs excepted), checks its own Zed permission on `auth/user`
(`set_active`, `rename`, `issue_password`, `reset_password`,
`issue_sign_in_link`; admin by default)
and refuses protected accounts: the actor's own, staff, superusers and effective
members of `iam/protected:main#member`. Issue and reset return the username with
a one-time secret, shown only in a read-only `usePrompt` reveal with a copy
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

## Sign-in links

A person can sign in without a password through an emailed link that works once.
`User.issue_sign_in_link()` is an account verb like `issue_password`: it locks
the row, checks the `issue_sign_in_link` permission (admin by default, unioned
into `active_account` and `nonstaff_account`) and refuses protected, inactive,
service and emailless accounts. It stores a `SignInLink` row with a keyed digest
of the token, bound to the account and its email, never the token itself. Once
that row commits, the link goes to the account's `email` through Django's
default mailer as plain text. The row then records `sent_at`, and
`sign_in_link_sent` fires with the account and the row. The issuer never sees
the link, and `created_by` records who issued it. System callers issue under
`system_context`, which leaves `created_by` empty. The console mutation is
`issue_user_sign_in_link` (an `ActionResult`), offered through
`account_actions` as `issue_sign_in_link`.

Only the account's newest link signs in, so issuing again retires older links;
rows stay as the issue record. A link expires after `ANGEE_IAM_SIGN_IN_LINK_TTL`
seconds (three days by default). It stops working if the account's email
changes, or if the account becomes inactive, staff, a superuser or otherwise
protected. Redemption is Django's
`authenticate(request, sign_in_token=...)` through `SignInLinkBackend`, so
django-axes counts and locks out failed links as it does passwords. Django
masks the credential before `user_login_failed` receivers see it. The public
mutation `redeem_sign_in_link(token)` signs the person in; every refusal is the
same `ok: false`.

Links open `ANGEE_PUBLIC_ORIGIN` + `/sign-in-link#token=...`. The token rides
the fragment, which no request, access log or referrer carries. IAM's public
`/sign-in-link` route renders the login page, with the link as its sign-in
method. It clears the token from the address bar and redeems it only when the
person clicks, so a mail scanner that opens the link does not spend it. The
password form stays below it as the fallback. A deployment without a usable
`ANGEE_PUBLIC_ORIGIN` (`https://host`, no path) or a `MAILERS["default"]`
mailer offers no link and refuses the verb. Turn off the email provider's click
tracking for these messages: a tracking redirect would record the link.
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
