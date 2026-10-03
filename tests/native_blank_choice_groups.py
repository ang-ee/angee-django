"""Country grouping over the real composed parties model contracts."""

from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TransactionTestCase
from rebac import actor_context, system_context
from strawberry_django_aggregates import AggregateBuilder, ChoicesValueNotInEnumError

from angee.graphql.schema import GraphQLSchemas

Party = apps.get_model("parties", "Party")
Address = apps.get_model("parties", "Address")


class BlankCountryGroupsTests(TransactionTestCase):
    """Blank tax and postal countries remain enum keys with string drills."""

    def test_party_billing_and_address_blank_buckets(self):
        reader = get_user_model().objects.create_user(username="country-reader")
        with system_context(reason="test.blank_country_groups.seed"):
            party = Party.objects.create(display_name="Blank country", created_by=reader)
            known_party = Party.objects.create(display_name="Known country", tax_country="DE", created_by=reader)
            Address.objects.create(party=party, country="", created_by=reader)
            Address.objects.create(party=known_party, country="DE", created_by=reader)

        schema = GraphQLSchemas.from_discovery().build("blank_choices")
        resources = {item.model_label: item for item in schema.angee_resources}
        request = RequestFactory().post("/graphql/blank_choices/")
        request.user = reader
        for model, name, field in (
            (Party, "billing_parties", "tax_country"),
            (Address, "country_addresses", "country"),
        ):
            with self.subTest(model=model._meta.label):
                metadata = resources[model._meta.label]
                axis = metadata.query.axes[field]
                self.assertEqual([(item.from_value, item.to_value) for item in axis.server.value_map], [("BLANK", "")])
                self.assertEqual(axis.drill.value_map, axis.server.value_map)
                self.assertIn("", [value.value for value in metadata.query.fields[field].values])
                with actor_context(reader):
                    result = schema.execute_sync(f"""
                        query {{
                          groups: {name}_groups(group_by: [{{field: {field.upper()}}}]) {{
                            key {{ {field} }} aggregate {{ count }}
                          }}
                          blank: {name}(where: {{{field}: {{_eq: ""}}}}) {{ {field} }}
                          null: {name}(where: {{{field}: {{_is_null: true}}}}) {{ {field} }}
                        }}
                    """, context_value=SimpleNamespace(request=request))
                self.assertIsNone(result.errors)
                data = result.data
                self.assertEqual({item["key"][field]: item["aggregate"]["count"] for item in data["groups"]},
                                 {"BLANK": 1, "DE": 1})
                self.assertEqual(data["blank"], [{field: ""}])
                self.assertEqual(data["null"], [])

                builder = AggregateBuilder(model, group_by_fields=[field])
                key_type = builder.build().group_key_type
                spec = [(field, None)]
                self.assertEqual(getattr(builder.shape_group_key(key_type, {field: ""}, spec), field).value, "")
                self.assertIsNone(getattr(builder.shape_group_key(key_type, {field: None}, spec), field))
                with self.assertRaises(ChoicesValueNotInEnumError):
                    builder.shape_group_key(key_type, {field: "ZZ"}, spec)
