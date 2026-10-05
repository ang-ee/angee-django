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

## Managed people

Saved User records inherit IAM's account verbs as `iam.User#actions-menu`
children: deactivate/reactivate (`set_user_active`), rename (`rename_user`,
name fields only), give access (`issue_user_password`) and reset access
(`reset_user_password`). Each takes a confirmation and the revision the client
read (issue excepted), checks its own Zed permission on `auth/user`
(`set_active`, `rename`, `issue_password`, `reset_password`; admin by default)
and refuses protected accounts: the actor's own, staff, superusers and effective
members of `iam/protected:main#member`. Issue and reset return the username with
a one-time secret, shown only in a read-only `usePrompt` reveal with a copy
control; nothing reaches a record or query cache. Controls read the row's
`account_actions` projection, which batches the verbs' own conditions.

The generic update runs through `write` plus `write__<field>` gates: identity,
credential and authority columns stay with administrators, and a protected
account also needs `administer`. `last_login` reads through `read__last_login`.
The users list renders `iam.users#columns` (username, email, staff, active,
last sign-in) and ships the `iam.users.active` and `iam.users.deactivated`
presets. A consumer grants its manager role these powers and marks the role as
elevated from its own fragment, as
[`tests/extcontrib`](../../../tests/extcontrib/permissions.extends.zed) does:

```zed
definition auth/user {
    relation manager: consumer/role // rebac:const=manager
    permission read = manager->effective_member
    permission write = manager->effective_member
    permission set_active = manager->effective_member
    permission rename = manager->effective_member
    permission issue_password = manager->effective_member
    permission reset_password = manager->effective_member
    permission read__last_login = manager->effective_member
}
definition iam/protected {
    relation manager: consumer/role // rebac:const=manager
    permission member = manager->effective_member
}
```

## Capabilities

A capability names what some actors have and everyone else lacks, such as a
page that is absent for them: no rail or menu entry, no route, no data. Each is
a permission on the singleton
`iam/capability:main`; the definition is open (`@rebac_open_definitions`), so a
consumer declares its own from its `permissions.extends.zed`, and a later
contributor naming the same capability unions an arm in. Admin reach is the
`admin` relation; union it in when administrators should hold the capability:

```zed
definition iam/capability {
    relation manager: consumer/role // rebac:const=manager
    permission manage_people = manager->effective_member
}
```

`current_user.capabilities` lists the ones the session's identity (the viewed
one in a preview) holds, from one bulk engine check, so group-held grants, role
hierarchies and live rosters count, unlike `role_refs`. Anonymous sessions hold
none. The web identity carries them as `AuthUser.capabilities`, refreshed with
every identity load: sign-in, sign-out, preview changes. A menu entry or route
names one in `requires`:

```ts
routes: [{ name: "people.manage", path: "/people/manage", requires: "manage_people", component: ManagePeople }],
menus: { "people.manage": { parent: "people", route: "people.manage", requires: "manage_people" } },
```

The data stays guarded by the resources' own permissions; `requires` only
decides presence. `declared_capabilities` names every declared capability for a
signed-in session; in development the shell warns about a `requires` naming
none of them.
