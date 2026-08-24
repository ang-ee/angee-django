"""Framework job seam backed by Celery.

This app owns Angee's deferred and periodic execution tier. Addons declare
Celery tasks in conventional ``tasks.py`` modules and enqueue through the small
Angee seam when they need framework-owned defaults.

Kept import-light because Django imports the package during app population.
Callers import the concrete seam from ``angee.jobs.enqueue`` or
``angee.jobs.locks``; Celery imports ``angee.jobs.celery`` as its application.
"""
