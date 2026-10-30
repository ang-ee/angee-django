"""The VCS source tier of the addon marketplace.

Platform projects installed distributions into its one ``Addon`` registry. This
addon extends those rows with VCS source/path provenance and owns declarations
not installed in the project, created ``REMOTE``/``DISABLED`` and marked
``REMOVED`` when they disappear from their source.

An addon ``Source`` obtains a complete pinned checkout from the operator, or the
current working tree from a local backend. Each package is built in native PyPA
isolation; django-compose indexes its wheel distributions and Platform projects
the same metadata facts as installed packages. Authored TOML supplies capability
facts only. Addon code is never imported into the serving process.

Remote checkout lifetime, submodules and Git credentials belong to the operator;
Django and the operator must share the stack filesystem. Any checkout, build or
identity failure preserves the previous catalogue and sync timestamp. Network and
build work finish before the transaction that validates source ownership and
reconciles rows. Platform retains installed-row precedence; this addon adds only
provenance to those rows.
"""
