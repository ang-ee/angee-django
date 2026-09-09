"""Focused contracts for bounded, model-free structured invoice parsing."""

from __future__ import annotations

import io

import pytest

from angee.workflows_ocr.structured import (
    StructuredLimits,
    StructuredSourceError,
    extract_structured_sources,
)


def _values(source):
    return {(fact.field, fact.occurrence): fact.raw_value for fact in source.facts}


def test_ubl_copies_raw_header_line_and_bank_facts_without_validation_claims() -> None:
    payload = b"""<?xml version="1.0"?>
    <Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
      xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
      xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
      <cbc:UBLVersionID>2.1</cbc:UBLVersionID><cbc:ID>INV-7</cbc:ID>
      <cbc:IssueDate>2026-09-01</cbc:IssueDate><cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
      <cac:AccountingSupplierParty><cac:Party><cac:PartyName>
        <cbc:Name>Example GmbH</cbc:Name>
      </cac:PartyName><cac:PostalAddress><cbc:StreetName>Supplierstrasse 4</cbc:StreetName>
        <cbc:CityName>Berlin</cbc:CityName><cbc:PostalZone>10115</cbc:PostalZone>
        <cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country>
      </cac:PostalAddress></cac:Party></cac:AccountingSupplierParty>
      <cac:AccountingCustomerParty><cac:Party><cac:PostalAddress>
        <cbc:StreetName>Buyer Road 9</cbc:StreetName><cbc:CityName>Paris</cbc:CityName>
      </cac:PostalAddress></cac:Party></cac:AccountingCustomerParty>
      <cac:PaymentMeans><cac:PayeeFinancialAccount><cbc:ID>DE02120300000000202051</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>
      <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="EUR">12.30</cbc:PayableAmount></cac:LegalMonetaryTotal>
      <cac:InvoiceLine><cbc:InvoicedQuantity>2</cbc:InvoicedQuantity><cac:Item><cbc:Name>Hosting</cbc:Name></cac:Item><cac:Price><cbc:PriceAmount>6.15</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
    </Invoice>"""
    (source,) = extract_structured_sources(payload, media_type="application/xml", filename="invoice.xml")
    assert source.kind == "ubl_invoice"
    assert source.evidence.standard_version == "2.1"
    assert source.evidence.validated is False
    assert source.evidence.validator is None
    assert _values(source) == {
        ("invoice.reference", 0): "INV-7",
        ("invoice.issue_date", 0): "2026-09-01",
        ("invoice.currency", 0): "EUR",
        ("invoice.document_total", 0): "12.30",
        ("vendor.name", 0): "Example GmbH",
        ("vendor.address.street", 0): "Supplierstrasse 4",
        ("vendor.address.city", 0): "Berlin",
        ("vendor.address.postal_code", 0): "10115",
        ("vendor.address.country", 0): "DE",
        ("bank.account_number", 0): "DE02120300000000202051",
        ("line.description", 0): "Hosting",
        ("line.quantity", 0): "2",
        ("line.unit_price", 0): "6.15",
    }


def test_cii_maps_repeated_lines_by_occurrence() -> None:
    payload = b"""<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
      xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
      <rsm:ExchangedDocument><ram:ID>CII-9</ram:ID></rsm:ExchangedDocument>
      <rsm:SupplyChainTradeTransaction>
        <ram:ApplicableHeaderTradeAgreement><ram:SellerTradeParty><ram:PostalTradeAddress>
          <ram:LineOne>Seller Lane 8</ram:LineOne><ram:CityName>Hamburg</ram:CityName>
          <ram:PostcodeCode>20095</ram:PostcodeCode>
        </ram:PostalTradeAddress></ram:SellerTradeParty><ram:BuyerTradeParty><ram:PostalTradeAddress>
          <ram:LineOne>Buyer Lane 3</ram:LineOne>
        </ram:PostalTradeAddress></ram:BuyerTradeParty></ram:ApplicableHeaderTradeAgreement>
        <ram:IncludedSupplyChainTradeLineItem><ram:SpecifiedTradeProduct><ram:Name>One</ram:Name></ram:SpecifiedTradeProduct><ram:SpecifiedLineTradeDelivery><ram:BilledQuantity>3</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery></ram:IncludedSupplyChainTradeLineItem>
        <ram:IncludedSupplyChainTradeLineItem><ram:SpecifiedTradeProduct><ram:Description>Two</ram:Description></ram:SpecifiedTradeProduct></ram:IncludedSupplyChainTradeLineItem>
      </rsm:SupplyChainTradeTransaction>
    </rsm:CrossIndustryInvoice>"""
    (source,) = extract_structured_sources(payload)
    assert source.kind == "cii_cross_industry_invoice"
    assert _values(source)["line.description", 0] == "One"
    assert _values(source)["line.description", 1] == "Two"
    assert _values(source)["line.quantity", 0] == "3"
    assert _values(source)["vendor.address.street", 0] == "Seller Lane 8"
    assert _values(source)["vendor.address.city", 0] == "Hamburg"
    assert ("vendor.address.country", 0) not in _values(source)


def test_xml_entities_and_resource_excess_are_rejected_before_mapping() -> None:
    with pytest.raises(StructuredSourceError, match="entity declarations"):
        extract_structured_sources(b'<!DOCTYPE Invoice SYSTEM "invoice.dtd"><Invoice/>')
    with pytest.raises(StructuredSourceError, match="entity declarations"):
        extract_structured_sources(
            b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><Invoice>&leak;</Invoice>'
        )
    utf16_entity = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY leak "secret">]><Invoice>&leak;</Invoice>'.encode(
        "utf-16"
    )
    with pytest.raises(StructuredSourceError, match="entity declarations"):
        extract_structured_sources(utf16_entity, media_type="application/xml")
    deep = b"<Invoice>" + b"<x>" * 4 + b"v" + b"</x>" * 4 + b"</Invoice>"
    with pytest.raises(StructuredSourceError, match="parsing limits"):
        extract_structured_sources(deep, limits=StructuredLimits(max_xml_depth=3))


def test_edifact_invoic_extracts_unambiguous_raw_facts_and_bounds_segments() -> None:
    payload = (
        b"UNB+UNOC:3+SENDER+RECEIVER+260909:1200+1'UNH+1+INVOIC:D:01B:UN'"
        b"BGM+380+EDI-4+9'DTM+137:20260909:102'CUX+2:EUR'MOA+39:42.50'"
        b"NAD+SU+9988::92++Supplier Ltd+Suite 2:6 Lansing Sq+North York+Ontario+M2J 1T5+'"
        b"LIN+1'IMD+F++:::Service'QTY+47:2'PRI+AAA:21.25'UNT+9+1'UNZ+1+1'"
    )
    (source,) = extract_structured_sources(payload, filename="invoice.edi")
    assert source.kind == "edifact_invoic"
    assert _values(source)["invoice.reference", 0] == "EDI-4"
    assert _values(source)["invoice.document_total", 0] == "42.50"
    assert _values(source)["vendor.identifier", 0] == "9988"
    assert _values(source)["vendor.address.street", 0] == "Suite 2"
    assert _values(source)["vendor.address.extended", 0] == "6 Lansing Sq"
    assert _values(source)["vendor.address.city", 0] == "North York"
    assert _values(source)["vendor.address.region", 0] == "Ontario"
    assert _values(source)["vendor.address.postal_code", 0] == "M2J 1T5"
    assert ("vendor.address.country", 0) not in _values(source)
    assert _values(source)["line.description", 0] == "Service"
    with pytest.raises(StructuredSourceError, match="segment exceeds"):
        extract_structured_sources(payload, limits=StructuredLimits(max_edi_segment_length=10))


def test_non_invoice_edi_is_retained_as_unsupported_structured() -> None:
    payload = b"UNB+UNOC:3+A+B+260909:1200+1'UNH+1+ORDERS:D:01B:UN'UNT+2+1'UNZ+1+1'"
    (source,) = extract_structured_sources(payload)
    assert source.kind == "unsupported_structured"
    assert source.facts == ()
    assert "not INVOIC" in source.review_reasons[0]


def test_complex_or_multi_message_edifact_is_retained_for_review() -> None:
    escaped = b"UNB+UNOC:3+A+B+260909:1200+1'UNH+1+INVOIC:D:01B:UN'BGM+380+INV?+1'UNT+2+1'UNZ+1+1'"
    (source,) = extract_structured_sources(escaped)
    assert source.kind == "unsupported_structured"
    assert "release-character" in source.review_reasons[0]
    multi = b"UNB+UNOC:3+A+B+260909:1200+1'UNH+1+INVOIC:D:01B:UN'UNT+2+1'UNH+2+INVOIC:D:01B:UN'UNT+2+2'UNZ+2+1'"
    (source,) = extract_structured_sources(multi)
    assert source.kind == "unsupported_structured"
    assert "Multiple EDIFACT" in source.review_reasons[0]


def test_x12_810_copies_big_it1_and_pid_but_does_not_guess_tds_decimal() -> None:
    isa = "ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260909*1200*U*00401*000000001*0*P*>~"
    payload = (
        isa + "GS*IN*SENDER*RECEIVER*20260909*1200*1*X*004010~ST*810*0001~"
        "BIG*20260909*X12-8~CUR*BY*USD~N1*SE*Supplier Inc*92*SUP-1~"
        "IT1*1*2*EA*10.25**VP*SKU~PID*F****Monthly service~TDS*2050~SE*8*0001~GE*1*1~IEA*1*000000001~"
    ).encode()
    (source,) = extract_structured_sources(payload, filename="invoice.x12")
    assert source.kind == "x12_810"
    assert _values(source)["invoice.reference", 0] == "X12-8"
    assert _values(source)["line.quantity", 0] == "2"
    assert _values(source)["line.unit_price", 0] == "10.25"
    assert _values(source)["invoice.document_total", 0] == "2050"
    assert _values(source)["vendor.identifier", 0] == "SUP-1"
    assert "implied decimal scale" in source.review_reasons[0]


def test_plain_text_is_not_misclassified_as_structured() -> None:
    assert extract_structured_sources(b"Invoice 42 is due next week", filename="message.txt") == ()


def test_signed_structured_carrier_is_retained_for_review_without_decoding() -> None:
    (source,) = extract_structured_sources(b"opaque signed content", filename="invoice.xml.p7m")
    assert source.kind == "unsupported_structured"
    assert "Signed or compressed" in source.review_reasons[0]


def test_bom_plaintext_is_not_xml_and_declared_malformed_edi_requires_review() -> None:
    assert extract_structured_sources(b"\xef\xbb\xbfplain invoice text", filename="message.txt") == ()
    (source,) = extract_structured_sources(b"not an interchange", filename="invoice.edi")
    assert source.kind == "unsupported_structured"
    assert "malformed envelope" in source.review_reasons[0]


def test_unknown_cii_and_extension_namespaces_cannot_supply_supported_facts() -> None:
    (source,) = extract_structured_sources(b'<x:CrossIndustryInvoice xmlns:x="urn:example:spoof"/>')
    assert source.kind == "unsupported_structured"
    ubl = b"""<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
      xmlns:ext="urn:example:spoof"><ext:ID>SPOOF</ext:ID></Invoice>"""
    (source,) = extract_structured_sources(ubl)
    assert source.kind == "ubl_invoice"
    assert source.facts == ()


def test_pdf_embedded_xml_is_enumerated_and_sized_before_extraction() -> None:
    import pypdfium2 as pdfium

    xml = b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"><ID>PDF-1</ID></Invoice>'
    document = pdfium.PdfDocument.new()
    document.new_page(612, 792)
    attachment = document.new_attachment("invoice.xml")
    attachment.set_data(xml)
    output = io.BytesIO()
    document.save(output)
    document.close()

    (source,) = extract_structured_sources(output.getvalue(), media_type="application/pdf", source_position=3)
    assert source.carrier == "pdf_attachment"
    assert source.name == "invoice.xml"
    assert source.evidence.source_position == 3
    assert source.evidence.payload_size == len(xml)
    assert _values(source)["invoice.reference", 0] == "PDF-1"

    with pytest.raises(StructuredSourceError, match="attachment exceeds"):
        extract_structured_sources(
            output.getvalue(),
            media_type="application/pdf",
            limits=StructuredLimits(max_attachment_bytes=len(xml) - 1),
        )
