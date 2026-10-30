"""Operator addon — browser bridge to the local Angee operator daemon.

Holds no Django models. It contributes one console GraphQL field
(``operatorConnection``, which mints a scoped browser token) and the REBAC schema
that gates it, plus the ``OperatorDaemon`` server-side bridge Django uses to drive
the daemon over its REST API. The daemon still owns all
stack/service/source/workspace lifecycle; this addon only reaches it.
"""
