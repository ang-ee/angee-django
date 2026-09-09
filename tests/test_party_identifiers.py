"""Local VAT/IBAN/BIC validation belongs to the shared parties models."""

import pytest
from django.core.exceptions import ValidationError

from tests.messaging_models import Bank, BankAccount, Party


@pytest.mark.parametrize(
    ("country", "value", "canonical"),
    [
        ("de", "DE136695976", "DE136695976"),
        ("NL", "NL123456782B12", "NL123456782B12"),
        ("LV", "LV40003521600", "LV40003521600"),
    ],
)
def test_vat_uses_country_validator(country, value, canonical):
    party = Party(display_name="Tax test", tax_country=country, vat=value)
    party.full_clean(validate_unique=False, validate_constraints=False)
    assert party.vat == canonical
    assert party.tax_country == country.upper()


@pytest.mark.parametrize(("country", "value"), [("DE", "DE136695977"), ("", "123"), ("DEU", "123")])
def test_invalid_vat_is_rejected(country, value):
    with pytest.raises(ValidationError):
        Party(tax_country=country, vat=value).clean()


def test_iban_canonicalization_and_bank_country():
    bank = Bank(pk=1, name="Bank", bic="DEUTDEFF")
    bank.clean()
    account = BankAccount(bank=bank, account_number="DE89 3704 0044 0532 0130 00")
    account.clean()
    assert account.account_number == "DE89370400440532013000"
    bank.country = "NL"
    with pytest.raises(ValidationError):
        account.clean()


def test_bad_bic_and_iban_are_rejected():
    with pytest.raises(ValidationError):
        Bank(name="Bank", bic="INVALID").clean()
    with pytest.raises(ValidationError):
        BankAccount(account_number="DE00370400440532013000").clean()


@pytest.mark.parametrize("country", ["US", "ZZ"])
def test_tax_identifier_without_vat_validator_is_retained(country):
    party = Party(display_name="International", tax_country=country, vat=" 123-456 ")
    party.full_clean(validate_unique=False, validate_constraints=False)
    assert party.vat == "123-456"


def test_optional_tax_inputs_accept_null_as_empty():
    party = Party(display_name="No tax identifier", tax_country=None, vat=None)
    party.full_clean(validate_unique=False, validate_constraints=False)
    assert (party.tax_country, party.vat) == ("", "")


@pytest.mark.parametrize(
    ("country", "number", "canonical"),
    [
        ("RU", "123456789047", "123456789047"),
        ("IN", "27AAPFU0939F1ZV", "27AAPFU0939F1ZV"),
        ("BR", "16.727.230/0001-97", "16727230000197"),
        ("AU", "83 914 571 673", "83914571673"),
        ("CA", "12302 6635", "123026635"),
        ("NO", "NO 995 525 828 MVA", "995525828MVA"),
    ],
)
def test_non_eu_identifiers_use_their_native_format(country, number, canonical):
    party = Party(display_name="International", tax_country=country, vat=number)
    party.full_clean(validate_unique=False, validate_constraints=False)
    assert party.vat == canonical
