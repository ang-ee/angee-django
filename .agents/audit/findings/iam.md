# IAM addon structural audit

Scope: `src/angee/iam`. Judged only against `AGENTS.md`, `docs/guidelines.md`,
`docs/backend/guidelines.md`, `docs/stack.md`, `docs/glossary.md`. Read-only.

- id: iam-001
  loc: src/angee/iam/models.py:46-63,750-778
  category: scattered-function / find-the-owner
  severity: high
  rule: docs/backend/guidelines.md "Domain behavior lives on models, managers, and querysets"; "a function that switches on a value's type is asking for polymorphism on that type"; AGENTS.md "Put behavior on the object that owns the data … a function that switches on a value's type wants polymorphism"
  finding: Five loose module functions (_capability_account_status, _rollup_account_status, _account_status, _account_status_precedence, _choice_value) plus two module dicts (_ACCOUNT_STATUS_PRECEDENCE, _CAPABILITY_ACCOUNT_STATUS) decode and rank AccountStatus values from outside the enum that owns them.
  fix: Move these onto AccountStatus (e.g. classmethod from_value/from_capability, a precedence property, and a classmethod rollup(values)); delete the module dicts and helpers, leaving note_capability_status to call the enum.
  status: fixed

- id: iam-002
  loc: src/angee/iam/identity.py:1-384
  category: wrong-primitive / scattered-function / find-the-owner
  severity: high
  rule: docs/backend/guidelines.md "Compose behavior onto the class that owns the data. When several functions take the same object and read, transform, or emit from it, that object should be a class and those functions its methods … not a module of loose functions"; "Management commands parse arguments and dispatch to the owning model, manager, service, or composer function"
  finding: identity.py is a module of loose functions (resolve, complete_login, complete_link, _find_user_by_email, _create_user_for_identity, _available_username, _link_state_user, ...) plus two passive frozen dataclasses (LoginCompletion, LinkCompletion); most branch on or read oauth_client/claims/User shape, and the user-resolution/provisioning logic belongs on the User manager (UserManager) and the ExternalAccount/Account manager, not a sibling module.
  fix: Move identity resolution/provisioning onto UserManager (e.g. resolve_oidc_identity / create_for_identity / find_by_email) and the OIDC completion onto an owning class; keep only thin orchestration loose, and make the LoginCompletion/LinkCompletion holders methods' return values rather than the home of behavior.
  status: fixed

- id: iam-003
  loc: src/angee/iam/schema.py:36-39
  category: deferred-import-idiom / framework-contract
  severity: medium
  rule: docs/backend/guidelines.md "Probe optional or generated modules with importlib.util.find_spec … rather than try/except ImportError, so an absent generated runtime/ reads as 'not built yet,' not a swallowed error"
  finding: The User model is resolved with `try: apps.get_model(...) except LookupError`, swallowing the not-built-yet case as an exception rather than probing for it; the same module then calls apps.get_model unguarded for the other four models, so the contract is inconsistent.
  fix: Resolve the missing-runtime case through the documented "not built yet" probe (or a single shared helper that all five model lookups use), matching the find_spec rule rather than a bare try/except LookupError.
  status: fixed

- id: iam-004
  loc: src/angee/iam/schema.py:383-443,682-713,793-988
  category: scattered-function / find-the-owner
  severity: medium
  rule: docs/backend/guidelines.md "Row-set behavior lives on managers and querysets"; "Domain behavior lives on models, managers, and querysets"; "Management commands parse arguments and dispatch to the owning model, manager"
  finding: Resolver/business helpers that decode model row-sets live loose in schema.py instead of on their managers: _available_connections (the public-picker filter+annotate predicate), _console_* (rebac_select_related join sets), and _would_remove_only_oidc_sign_in_method (the "only sign-in method" predicate over Credential) all read Credential/OAuthClient/ExternalAccount shape that the managers own (cf. CredentialManager.connected_for already on the manager).
  fix: Move the querystring predicates onto OAuthClientManager / ExternalAccountManager / CredentialManager (e.g. available_connections(), console_*(), is_only_oidc_sign_in(user)); the resolvers then dispatch to the manager.
  status: fixed

- id: iam-005
  loc: src/angee/iam/apps.py:28-34
  category: deferred-import
  severity: low
  rule: docs/backend/guidelines.md "Mark such a [phase-1] deferral with a comment naming the reason; everywhere else, hoist."
  finding: ready() defers `from angee.iam import signals` (a valid phase-1 / app-loading deferral) but carries no comment naming the reason, so it reads as an unexplained function-local import against the hoist rule.
  fix: Add the one-line phase-1/app-population reason comment on the deferred import, as the rule requires.
  status: fixed

- id: iam-006
  loc: src/angee/iam/management/commands/iam_oauth_clients.py:1
  category: naming-multiword-module
  severity: low
  rule: docs/backend/guidelines.md Naming "Modules are lowercase, single-word, named by role"; AGENTS.md "Make extension mechanical: named hooks, explicit owners"
  finding: The management command module is multi-word (iam_oauth_clients.py); the "iam_" prefix re-encodes the owning app already given by the package path.
  fix: Rename to a single-word command module under the iam command namespace (e.g. management/commands/oauth_clients.py), dropping the redundant addon prefix.
  status: fixed

- id: iam-007
  loc: src/angee/iam/schema.py:916-921 ; src/angee/iam/schema.py:99-115
  category: DRY / find-the-owner
  severity: low
  rule: AGENTS.md "Keep one source of truth per fact … Put behavior on the object that owns the data"; docs/guidelines.md DRY
  finding: _string_list re-coerces JSON list columns (default_scopes, scopes_catalogue, allowed_email_domains) in the GraphQL layer, and the OAuthClientType field resolvers each re-read the same OAuthClient JSON columns; the list-of-strings coercion is a persisted-field fact that the OAuthClient model (or the field) owns, decoded from outside here.
  fix: Expose the string-list columns as model properties/field coercion on OAuthClient and have the GraphQL type read them, removing the schema-local _string_list shape-decode.
  status: fixed

