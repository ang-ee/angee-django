"""Exercise a consumer donor without adding billing vocabulary to the framework."""

from django.db import models

from angee.parties.fields import CountryCodeField


class PartyBilling(models.Model):
    """The real blank tax-country declaration contributed onto Party."""

    extends = "parties.Party"
    tax_country = CountryCodeField(blank=True, default="")

    class Meta:
        abstract = True
