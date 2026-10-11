# Provenance

The addon stores attribution for logical writes. Addons composing history declare
provenance as a dependency; the composer emits its concrete
[`Changeset`](models.py). The [core boundary](../../../angee/base/changesets.py)
owns context and commit-aware persistence, without granting an authorization bypass.

The [permission schema](permissions.zed) owns direct access. Other actor types
contribute additive permission fragments from their own addons. Attribution never
grants access to touched records; history reads follow those records' permissions.

Storage follows Django's swappable-model convention, with the default owned by
[base autoconfig](../../../angee/base/autoconfig.py). Consumer history-FK migrations
must depend on `migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)`.
Django's `makemigrations` owns the initial schema; see the
[runtime migration policy](../../../docs/composer.md#addon-owned-runtime-migrations).
