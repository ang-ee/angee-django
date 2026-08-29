"""CardDAV directory backend for the parties addon.

Contributes only a ``Directory`` backend that discovers address books on a
CardDAV server and syncs their contacts into ``parties`` — no source models of
its own. Named per the ``Directory`` bridge through ``backend_class``, so the
dependency stays one-way (``parties_integrate_carddav`` → ``parties``).
"""
