"""Emailed sign-in links: an administrator sends a person a single-use link that signs them in.

It adds the ``issue_sign_in_link`` action to IAM's account actions and redeems the
link through a credential backend under IAM's login throttle. The session it
opens is IAM's own, recorded under ``angee.iam.auth.SESSION_BACKEND``.
"""
