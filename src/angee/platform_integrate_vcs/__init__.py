"""Read-only built-wheel inputs for Platform's native distribution catalogue.

Installed metadata remains with Platform. This addon adds Source provenance and
indexes explicit local or immutable remote wheel artifacts through native VCS
binary transport. Authors/CI own building; package indexes and installation stay
with the existing dependency workflow. The catalogue runs no backend or operator
job. Data preparation precedes the existing source CAS and reconciliation, so
failed or changed inputs preserve catalogue rows and their sync timestamp.
"""
