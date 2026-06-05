# Framework re-composition — Django-grounded packaging

Reorganize `angee/` from the spaghetti `base/` grab-bag into Django-shaped
subsystems. `base/` becomes the model layer (the `django.db` analog) only;
everything else moves to the subsystem that owns it.

## Target layout

```
angee/
├── apps.py          # AddonConfig (contract)            ~ django.apps   (was base/apps.py:BaseAddonConfig)
├── discovery.py     # discover_addons, addon_aliases                    (was base/discovery.py)
├── conf.py          # compose_defaults                  ~ django.conf   (was base/settings.py)
├── base/            # the MODEL TOOLKIT                  ~ django.db
│   ├── apps.py      #   BaseConfig(AddonConfig) — installed app; ready() wires audit+revision
│   ├── models.py    #   AngeeModel + managers
│   ├── mixins.py
│   ├── fields.py
│   ├── relations.py #   grant_owner/revoke_owner (REBAC model writes)
│   └── signals.py   #   audit stamping + revision registration ONLY
├── graphql/         # the GraphQL runtime (pure library — no AppConfig)
│   ├── schema.py introspection.py errors.py crud.py node.py subscriptions.py events.py
│   ├── access.py    #   (was base/access.py)
│   ├── deletion.py  #   (was base/deletion.py)
│   ├── publishing.py#   change_group/connect_publishers/_publish/_broadcast + json_safe (was base/signals.py publish half + base/serialization.py)
│   └── views.py urls.py asgi.py consumers.py             (was base/*)
└── compose/
    ├── apps.py runtime.py
    └── management/commands/schema.py                     (was base/management/commands/schema.py — calls graphql.render_sdl())

addons/angee/integrate/net.py    # was angee/base/net.py  (resources/fetch imports it from here)
addons/angee/resources/loader.py # keeps its own small json_safe copy
```

## Phases (each ends green: `uv run python -m pytest -q`) — ALL DONE ✅

Final state: 216 passed · `schema --check: ok` · `makemigrations --check`: no drift ·
mypy clean (25 files) · `angee build: ok`.

- [x] **A. net → integrate.** `angee/base/net.py` → `addons/angee/integrate/net.py`.
      integrate/{webhooks,models}, resources/fetch, tests now import `angee.integrate.net`.
- [x] **B. serialization eviction.** `angee/base/serialization.py` → `addons/angee/resources/serialization.py`
      (resources owns `json_safe`); `graphql/publishing.py` carries its own private `_json_safe`.
- [x] **C. contract + discovery → top level.** `BaseAddonConfig`→`AddonConfig` in `angee/apps.py`;
      `discover_addons` in `angee/discovery.py`. `BaseConfig` stays thin in `angee/base/apps.py`.
- [x] **D. compose_defaults → `angee/conf.py`** (logic unchanged for now).
- [x] **E. graphql → `angee/graphql/`.** graphql pkg + views/urls/asgi/consumers + deletion + access;
      `base/signals.py` split (publish half → `graphql/publishing.py`); schema command → compose.
      Host `urls.py`/`asgi.py` repointed to `angee.graphql.*`.
- [x] **F. layering tests** rewritten to the new boundaries (`base ⊥ {graphql, compose, addons}`,
      `graphql ⊥ compose`, `discovery → apps` only).
- [x] **post.** Regenerated the gitignored `runtime/integrate/migrations/0001` (it had baked
      `import angee.base.net`); now imports `angee.integrate.net`, no drift.

## Deferred (behavioral — separate follow-up, NOT this move)

Keeps `compose_defaults` hardcoded for now. Follow-up: make it a generic merge of addon
declarations, de-hardcode `resources.Resource` from the `base` label (resources owns
`runtime/resources/`), and move emit-contribution onto the mixins. These change emission +
need runtime regen + migration care, so they are sequenced after the structural move lands.
