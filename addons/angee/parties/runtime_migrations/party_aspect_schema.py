"""Frozen schema for identity, claimed rows and symmetric same-as statements.

The existing handle cutover is untouched. Helpers expand this migration's frozen
field declarations only; they do not import current model or generator policy.
Fresh hosts obtain the same schema through Django's native makemigrations.
"""

from copy import deepcopy

from django.conf import settings
from django.db import migrations, models
from simple_history.models import HistoricalChanges

from angee.parties.fields import CountryCodeField


def applies(state):
    if ("parties", "partystatement") not in state.models:
        return False
    required = {"personstatement", "organizationstatement", "addressstatement", "relationshipstatement",
                "partysameaspair", "partysameaspairstatement"}
    present = {name for name in required if ("parties", name) in state.models}
    if present == required:
        return False
    if present:
        raise ValueError("Party aspect schema is partial; reconcile the native migration state.")
    return True


def fk(to, *, null=True, protect=True):
    return models.ForeignKey(to, null=null, blank=null, on_delete=models.PROTECT if protect else models.DO_NOTHING,
                             related_name="+", db_constraint=protect)


def text():
    return models.TextField(null=True, blank=True)


def dates(names):
    return [(name, models.DateField(null=True, blank=True)) for name in names]


TRASH = [
    ("is_trashed", models.BooleanField(default=False, db_index=True, editable=False)),
    ("trashed_at", models.DateTimeField(null=True, blank=True, editable=False)),
    ("trashed_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)),
    ("trash_reason", models.TextField(blank=True, default="", editable=False)),
]
AUDIT = [
    ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
    ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
    ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")),
    ("updated_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")),
]
ADDRESS = [
    ("label", models.CharField(max_length=64, null=True, blank=True)),
    ("po_box", models.CharField(max_length=128, null=True, blank=True)),
    *[(name, text()) for name in ("extended", "street", "city", "region")],
    ("postal_code", models.CharField(max_length=32, null=True, blank=True)),
    ("country", CountryCodeField(null=True, blank=True)),
    ("latitude", models.FloatField(null=True, blank=True)),
    ("longitude", models.FloatField(null=True, blank=True)),
]
RELATIONSHIP = [*[(name, text()) for name in ("other_name", "title", "notes")],
                *dates(("started_at", "ended_at"))]
STANCE = [
    ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
    ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
    ("asserted_by", models.TextField()),
    ("source_kind", models.CharField(max_length=16, db_index=True)),
    ("snak", models.CharField(max_length=10, db_index=True, default="value")),
    ("confidence", models.FloatField(default=1.0)),
    ("rank", models.CharField(max_length=9, db_index=True, default="normal")),
    ("status", models.CharField(max_length=11, db_index=True, default="asserted")),
    ("applied", models.BooleanField(default=False)),
    ("evidence", models.JSONField(default=list, blank=True)),
    ("basis_hash", models.CharField(max_length=64, blank=True)),
    ("changeset", fk(settings.ANGEE_CHANGESET_MODEL)),
    ("link", fk("integrate.recordlink")),
]


def history(name, fields):
    copied = deepcopy(fields)
    for column, field in copied:
        if isinstance(field, models.ForeignKey):
            field.null = field.blank = True
            field.db_constraint = False
            field.remote_field.on_delete = models.DO_NOTHING
        elif isinstance(field, models.DateTimeField):
            field.auto_now = field.auto_now_add = False
            field.editable = False
            field.blank = True
    copied = [(column, models.BigIntegerField(db_index=True) if column == "id" else field) for column, field in copied]
    copied.extend([
        ("history_id", models.AutoField(primary_key=True)),
        ("history_date", models.DateTimeField(db_index=True)),
        ("history_type", models.CharField(max_length=1, choices=(("+", "Created"), ("~", "Changed"), ("-", "Deleted")))),
        ("history_change_reason", models.TextField(null=True)),
        ("history_user", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")),
    ])
    if not any(column == "changeset" for column, _ in copied):
        copied.append(("changeset", fk(settings.ANGEE_CHANGESET_MODEL)))
    return migrations.CreateModel(name="Historical" + name, fields=copied,
                                  bases=(HistoricalChanges, models.Model),
                                  options={"ordering": ("-history_date", "-history_id"),
                                           "get_latest_by": ("history_date", "history_id")})


def statements(subject, aspects, columns):
    name = subject + "Statement"
    fields = [("id", models.BigAutoField(primary_key=True)), *deepcopy(STANCE), *deepcopy(columns),
              ("subject", fk("parties." + subject.lower(), null=False)),
              ("superseded", fk("parties." + name.lower())),
              ("aspect", models.CharField(max_length=64, choices=[(key, key) for key in aspects]))]
    constraints = [
        models.CheckConstraint(condition=models.Q(aspect__in=aspects), name=f"parties_{name.lower()}_aspect"),
        models.CheckConstraint(condition=models.Q(confidence__gte=0, confidence__lte=1)
                               & (models.Q(rank="normal") | models.Q(rank="preferred", source_kind="person"))
                               & models.Q(snak__in=("value", "some_value", "no_value"),
                                          status__in=("asserted", "withdrawn", "quarantined", "deprecated"),
                                          source_kind__in=("person", "system_of_record", "directory", "network", "import", "seed", "document", "extraction", "rule")),
                               name=f"parties_{name.lower()}_stance"),
    ]
    for aspect in aspects:
        current = models.Q(aspect=aspect, superseded__isnull=True)
        for suffix, key, condition in (
            ("key", ("asserted_by", "subject", "aspect"), current),
            ("applied", ("subject", "aspect"), current & models.Q(applied=True)),
        ):
            constraints.append(models.UniqueConstraint(fields=key, condition=condition,
                                                       name=f"parties_{name.lower()}_{suffix}_{aspect}"))
    return [migrations.CreateModel(name=name, fields=fields,
                                   options={"db_table": f"parties_{subject.lower()}_statement", "constraints": constraints,
                                            "base_manager_name": "_angee_base"},
                                   managers=[("objects", models.Manager()), ("_angee_base", models.Manager())]),
            history(name, fields)]


operations = []
for name in ("party", "historicalparty", "historicalperson", "historicalorganization", "address", "relationship"):
    for column, field in TRASH:
        copied = deepcopy(field)
        if name.startswith("historical") and isinstance(copied, models.ForeignKey):
            copied.remote_field.on_delete = models.DO_NOTHING
            copied.db_constraint = False
        operations.append(migrations.AddField(model_name=name, name=column, field=copied))
for column, field in [("display_name", text()), ("notes", text()), ("avatar", fk("storage.file"))]:
    for name in ("partystatement", "historicalpartystatement"):
        copied = deepcopy(field)
        if name.startswith("historical") and isinstance(copied, models.ForeignKey):
            copied.remote_field.on_delete = models.DO_NOTHING
            copied.db_constraint = False
        operations.append(migrations.AddField(model_name=name, name=column, field=copied))
for name in ("partystatement", "historicalpartystatement"):
    operations.append(migrations.AlterField(model_name=name, name="aspect", field=models.CharField(
        max_length=64, choices=[(key, key) for key in ("contact", "handle", "identity")],
    )))
operations.extend([migrations.RemoveConstraint(model_name="partystatement", name="parties_partystatement_aspect"),
                   migrations.AddConstraint(model_name="partystatement", constraint=models.CheckConstraint(
                       condition=models.Q(aspect__in=("contact", "handle", "identity")), name="parties_partystatement_aspect"))])
for aspect in ("contact", "identity"):
    current = models.Q(aspect=aspect, superseded__isnull=True)
    for suffix, key, condition in (("key", ("asserted_by", "subject", "aspect"), current),
                                   ("applied", ("subject", "aspect"), current & models.Q(applied=True))):
        operations.append(migrations.AddConstraint(model_name="partystatement", constraint=models.UniqueConstraint(
            fields=key, condition=condition, name=f"parties_partystatement_{suffix}_{aspect}")))
operations.extend(statements("Person", ("dates", "name"), [
    *dates(("birthday", "anniversary")),
    *[(name, text()) for name in ("name_prefix", "given_name", "additional_name", "family_name", "name_suffix", "nickname")],
]))
operations.extend(statements("Organization", ("legal_identity",), [("legal_name", text()),
                                                                  ("domain", models.CharField(max_length=255, null=True, blank=True))]))
operations.extend(statements("Address", ("address",), ADDRESS))
operations.extend(statements("Relationship", ("relationship",), RELATIONSHIP))
for name, columns, links in (
    ("Address", ADDRESS, [("party", fk("parties.party")), ("is_primary", models.BooleanField(default=False))]),
    ("Relationship", RELATIONSHIP, [("party", fk("parties.party")), ("other_party", fk("parties.party")),
                                     ("kind", fk("parties.relationshipkind"))]),
):
    columns = deepcopy(columns)
    for _, field in columns:
        if field.null and not isinstance(field, models.ForeignKey):
            field.null = False if not isinstance(field, (models.DateField, models.FloatField)) else True
            if isinstance(field, (models.CharField, models.TextField)):
                field.default = ""
    fields = [("id", models.BigAutoField(primary_key=True)), *deepcopy(AUDIT), *deepcopy(TRASH), *columns, *links]
    operations.append(history(name, fields))
pair_fields = [("id", models.BigAutoField(primary_key=True)), *deepcopy(AUDIT[:2]), *deepcopy(TRASH),
               ("left", fk("parties.party", null=False)), ("right", fk("parties.party", null=False))]
operations.extend([
    migrations.CreateModel(name="PartySameAsPair", fields=pair_fields,
                           options={"base_manager_name": "_angee_base", "constraints": [
                               models.UniqueConstraint(fields=("left", "right"), name="parties_partysameaspair_pair"),
                               models.CheckConstraint(condition=models.Q(left__lt=models.F("right")), name="parties_partysameaspair_order"),
                           ]}, managers=[("objects", models.Manager()), ("_angee_base", models.Manager())]),
    history("PartySameAsPair", pair_fields),
    *statements("PartySameAsPair", ("same_as",), []),
])


class Migration(migrations.Migration):
    dependencies = [("integrate", "__latest__"), ("storage", "__latest__"),
                    migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)]
    operations = operations
