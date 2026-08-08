# `odee.*` — consumer addons of the Odee ERP

Home of the Odee domain addons (`odee.product`, `odee.stock`, …), reconstructed
from the Odoo business ontology distilled by the odoo2angee IR project — a
coverage checklist, not a code generator. Each addon composes the framework's
shared primitives and the foundation base addons (`angee.money`, `angee.uom`,
`angee.parties`, `angee.storage`, …); capability gaps are fixed at the owning
level, never hand-rolled here. Build decisions and per-element transfer state
live in that project's `build/state.yaml`, keyed by natural Odoo keys.
