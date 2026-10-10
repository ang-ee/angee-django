"""Choose the installation's administrator explicitly."""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from rebac import system_context

from angee.iam.deployment import rebind_deployment_operator
from angee.resources.exceptions import ResourceLoadError


class Command(BaseCommand):
    help = "Rebind iam.deployment_operator to an existing active human administrator."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        model = get_user_model()
        with system_context(reason="iam.deployment_operator.command"):
            try:
                user = model._default_manager.get(**{model.USERNAME_FIELD: options["username"]})
                rebind_deployment_operator(user)
            except model.DoesNotExist as error:
                raise CommandError("The selected user does not exist.") from error
            except ResourceLoadError as error:
                raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"deployment operator: '{options['username']}'"))
