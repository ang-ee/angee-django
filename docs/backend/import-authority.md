# Import authority and external ownership

`angee.base.importing` owns the authorization boundary for internal importers.
It is separate from REBAC's `system_context`: a reason string records why code ran,
but never authorizes an externally owned write.

This is a model-foundation contract, alongside `angee.base.sync` and
`angee.base.ingress`. It depends on Django and the shared model toolkit, with no
dependency on integration connections, credentials, bridges, jobs, or domain
models. `angee.base.apps.BaseConfig` registers its checks and relation guards;
`angee.base.ownership` compiles its persistence guards. Domain addons own the
permitted operations and their invariants. Integration adapters call those domain
interfaces and supply the scoped authority.

Create an `ImportOperation` only after the integration owner has authenticated the
source, resolved the local company, and created its durable run. Open it inside the
same database transaction as the imported writes:

```python
with transaction.atomic(), import_operation(
    ImportOperation(
        source=ImportSource("integrate_odoo.odooinstance", str(instance.pk)),
        company=ImportCompany(company._meta.label_lower, str(company.pk)),
        operation="accounting.document.import",
        run=str(run.pk),
        targets=(ImportTarget("accounting.invoice"), ImportTarget("accounting.journalentry")),
    )
):
    Invoice.objects.import_posted(...)
```

Owned source models inherit `ExternalOwnershipMixin` and declare every concrete
and many-to-many field once with `ExternalOwnershipDeclaration`. Fields are denied
by default on owned rows. `local_fields` is the explicit overlay allow-list;
`protected_relations` also rejects an unclaimed edge when its endpoint is owned.
An `extends` donor adds policy with `ExternalOwnershipContribution`, which the
runtime's static donor-first MRO composes deterministically.

Consumer runtime migrations install the matching persistence guards with
`install_external_ownership_guard`. Many-to-many through tables use
`install_external_ownership_relation_guard`. Both accept an explicit declaration
for historical migration models. PostgreSQL guards call the stable
`angee_import_scope_allows(text, text, text, text, text, text, text)` predicate;
the final argument is the allowed operation names joined with `chr(31)`.

Company scope may be a direct foreign key or a static path such as
`entry__company`. Multi-table inherited targets receive a guard on every physical
table that contains source-owned fields; the compiler reads provenance and company
scope through the parent link.

Domain policy asks the target owner rather than decoding context or provenance:
`target.require_external_import(operation)` raises on mismatch and
`target.is_authorized_import(operation)` returns a boolean.
