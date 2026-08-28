"""Angee test package.

Keep this package explicit so ``tests.settings`` always resolves to Angee's own
Django test settings, even when installed dependencies or local tooling expose
their own top-level ``tests`` package.
"""
