"""Exercise a consumer donor with a field unique to the blank-choice fixture."""

from django_compose.models import Composable

from angee.parties.fields import CountryCodeField


class PartyBlankChoice(Composable, extends="parties.Party"):
    """Contribute a blank country choice onto Party for grouping tests."""

    blank_choice_country = CountryCodeField(blank=True, default="")

    class Meta:
        abstract = True
