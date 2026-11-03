"""Statement declarations compose native simple-history extension contracts."""

from django.db import models
from django.test.utils import isolate_apps

from angee.provenance.engine.statements import (
    Aspect,
    Statement,
    StatementManager,
    StatementQuerySet,
    StatementRecords,
)


class CustomQuerySet(StatementQuerySet):
    def identity(self):
        return self.filter(aspect="identity")


class CustomManager(StatementManager):
    def identity(self):
        return self.get_queryset().identity()


class CustomStatement(Statement):
    class Meta(Statement.Meta):
        abstract = True

    def declaration_owner(self):
        return "custom"


def test_statement_generator_forwards_native_extension_contracts():
    with isolate_apps("tests.provenance_engine.statements_app"):
        declaration = StatementRecords(
            aspects={"identity": Aspect(fields=("label",))},
            bases=(CustomStatement,),
            history_manager=CustomManager,
            historical_queryset=CustomQuerySet,
            excluded_fields=("ignored",),
        )

        class DeclaredSubject(models.Model):
            label = models.CharField(max_length=40)
            ignored = models.CharField(max_length=40)
            statements = declaration

            class Meta:
                app_label = "statements_test"

        manager = DeclaredSubject.statements
        assert isinstance(manager, CustomManager)
        assert isinstance(manager.get_queryset(), CustomQuerySet)
        assert issubclass(manager.model, CustomStatement)
        assert manager.model().declaration_owner() == "custom"
        assert tuple(declaration.excluded_fields) == ("ignored",)
        assert "label" in {field.name for field in manager.model._meta.fields}
        assert "ignored" not in {field.name for field in manager.model._meta.fields}
