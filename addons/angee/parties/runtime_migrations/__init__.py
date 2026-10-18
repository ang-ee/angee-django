"""Retired party fold migrations: import targets of applied history only.

Stacks that applied the parties preflight (``0006_preflight_party_folds`` in a main-era runtime)
keep a migration file that imports these modules. The bodies ran once and were deleted with the
folds they completed; the modules remain importable so the applied graph loads. A call means a
stack is replaying retired history, which the composer never does.
"""
