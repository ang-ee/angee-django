"""Native Django command-discovery bridge for the optional IAM override.

The host precedes auth in INSTALLED_APPS; IAM follows its auth dependency.
Django therefore discovers this binding first. Retire the bridge when native
command precedence can select the IAM owner without the host binding.
"""

from django.apps import apps
from django.contrib.auth.management.commands.changepassword import Command as DjangoCommand

Command: type[DjangoCommand]
if apps.is_installed("angee.iam"):
    from angee.iam.management.commands.changepassword import Command as IAMCommand

    Command = IAMCommand
else:
    Command = DjangoCommand
