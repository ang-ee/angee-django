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

Saved User records inherit IAM's account actions as `iam.User#actions-menu`
children: deactivate/reactivate (`set_user_active`), rename (`rename_user`,
name fields only), give access (`issue_user_password`), reset access
(`reset_user_password`) and set a chosen password (`set_user_password`). Set
password confirms the new password locally; the other changing actions take an
explicit confirmation. The actions are an open set: `User.account_actions` names
them, and an addon contributes its own by extending it and
`User.account_action_blockers` through `super()`, running its action under
`User.lock_for_account_action`. Whatever its hook returns, the action first
meets IAM's person-kind and protected-account blockers
(`account_action_refusals`), which no override drops. It adds its `iam.User#actions-menu` child
(`AccountActionBar`, `offersAccountAction`), as
[`iam_messaging`](../iam_messaging/README.md)'s Send sign-in link does. Its
permission unions into `active_account_granted` or `nonstaff_account_granted`
when the action acts only on active or non-staff accounts; IAM derives
`active_account` and `nonstaff_account` from them and seals both
(`angee.iam.E004`). At start-up IAM refuses a composed action offered twice or
not declared on `auth/user` (`angee.iam.E005`). Changing actions take the revision the client read (the issue
actions excepted), check their own Zed permission on `auth/user`
(`set_active`, `rename`, `issue_password`, `reset_password`, `set_password`;
admin by default)
and refuse protected accounts: the actor's own, staff, superusers and effective
members of `iam/protected:main#member`, except Set password's administrative
admission described below. Issue and reset return the username with
a one-time secret, shown only in the shared `useSecretReveal` prompt with a copy
control; nothing reaches a record or query cache. Controls read the row's
`account_actions` projection, which batches the actions' own conditions and lists
each offered action by its Zed permission name.

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
refused. Issue password, Reset password and the other account actions retain their
protected-account refusals. A successful set invalidates the target's existing
session hashes and, through the account facts a link is bound to, retires
outstanding sign-in links. After commit, IAM logs one
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

## Login throttling

Every credential authenticates through Django's `authenticate()`, as the
request's one attempt (`angee.iam.auth.authenticate_once`): a second attempt in
the same HTTP request (aliased or batched fields) is refused with "Only one
sign-in attempt is allowed per request." before `authenticate()` runs.

IAM owns the axes policy in its autoconfig. `AXES_COOLOFF_TIME` lifts a lockout
after an hour, and nothing else does: a successful sign-in clears no failures,
so a client cannot reset its count by signing in to an account it holds. Axes
records each failure under the client's IP address and a username, which
`AXES_USERNAME_CALLABLE` sets: for a password, the sign-in name; for another
credential, the account it names as `sign_in_account`, unmasked (the string it
carries, for an account that does not exist). IAM leaves axes locking out by IP
address. A deployment that adds `username` to `AXES_LOCKOUT_PARAMETERS`
throttles each account under those keys, separately per credential, and never
under an empty username. A credential backend that refuses a genuine but spent
credential calls `mark_spent_credential`; `AXES_WHITELIST_CALLABLE`
(`spent_credential`) exempts that attempt only, only for credentials naming the
same account, and the mark ends with the attempt. A project that sets its own
whitelist callable loses that exemption.

Every session records `angee.iam.auth.SESSION_BACKEND`, IAM's `ModelBackend`,
whatever credential opened it: OIDC login and emailed sign-in links
([`iam_messaging`](../iam_messaging/README.md)) bind it, so a session outlives
its credential's backend.

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
