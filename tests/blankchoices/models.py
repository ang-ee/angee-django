"""Exercise a consumer donor without adding billing vocabulary to the framework."""

from django_compose.models import Composable

from angee.parties.fields import CountryCodeField


class PartyBilling(Composable, extends="parties.Party"):
    """The real blank tax-country declaration contributed onto Party."""

    tax_country = CountryCodeField(blank=True, default="")

    class Meta:
        abstract = True
