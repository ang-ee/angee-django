"""Native package metadata catalogue from isolated remote builds or built local wheels.

Remote sources resolve through their selected VCS connection and submit an
immutable revision to the operator's declared source-build job. Fetch credentials
exist only in its separate fetch phase; the prepared build image runs offline.
Only bounded native metadata and packaged capability TOML return to Django.
Local sources index explicitly built wheels under Source.path; they never build
working trees. Both paths use django-compose's native identity index.

Preparation finishes before source CAS and catalogue reconciliation. Failed,
unavailable or invalid metadata operations preserve prior rows and sync time.
Installed rows retain their native precedence; this addon adds their provenance.
"""
