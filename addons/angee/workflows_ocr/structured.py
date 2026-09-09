"""Bounded, model-free extraction of structured invoice source facts.

This module detects syntax and copies source strings.  It deliberately does
not validate business-rule profiles, infer absent values, or assign confidence.
"""

from __future__ import annotations

import ctypes
import hashlib
from dataclasses import dataclass
from typing import Literal

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

StructuredKind = Literal[
    "ubl_invoice", "cii_cross_industry_invoice", "edifact_invoic", "x12_810", "unsupported_structured"
]
StructuredCarrier = Literal["file", "pdf_attachment"]


@dataclass(frozen=True, slots=True)
class StructuredLimits:
    """Resource limits applied before and during structured-source parsing."""

    max_input_bytes: int = 25 * 1024 * 1024
    max_pdf_attachments: int = 16
    max_attachment_bytes: int = 5 * 1024 * 1024
    max_xml_depth: int = 32
    max_xml_elements: int = 20_000
    max_xml_text_bytes: int = 2 * 1024 * 1024
    max_edi_segments: int = 10_000
    max_edi_segment_length: int = 16_384


@dataclass(frozen=True, slots=True)
class StructuredFact:
    """One source string with its format-native evidence path."""

    field: str
    raw_value: str
    source_path: str
    occurrence: int = 0


@dataclass(frozen=True, slots=True)
class StructuredEvidence:
    """Syntax evidence for one detected payload; validation is explicit."""

    source_position: int
    attachment_name: str | None
    format_id: str
    standard_version: str | None
    payload_size: int
    validated: bool = False
    validator: str | None = None


@dataclass(frozen=True, slots=True)
class StructuredSource:
    """Detected structured payload and raw facts safe for a policy mapper."""

    kind: StructuredKind
    media_type: str
    carrier: StructuredCarrier
    name: str
    content_sha256: str
    facts: tuple[StructuredFact, ...]
    evidence: StructuredEvidence
    review_reasons: tuple[str, ...] = ()


class StructuredSourceError(ValueError):
    """A structured candidate exceeded a bound or was unsafe to parse."""


_UBL_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
_UBL_CAC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_UBL_CBC_NS = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_CII_ROOT = "CrossIndustryInvoice"
_CII_RSM_NS = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_CII_RAM_NS = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_STANDARD_NAMESPACES = {_UBL_NS, _UBL_CAC_NS, _UBL_CBC_NS, _CII_RSM_NS, _CII_RAM_NS}


def extract_structured_sources(
    data: bytes,
    *,
    media_type: str = "",
    filename: str = "",
    source_position: int = 0,
    limits: StructuredLimits | None = None,
) -> tuple[StructuredSource, ...]:
    """Detect and parse structured invoice payloads without external I/O."""

    bounds = limits or StructuredLimits()
    if len(data) > bounds.max_input_bytes:
        raise StructuredSourceError("Structured source exceeds the input byte limit.")
    if data.startswith(b"%PDF-") or media_type.lower() == "application/pdf":
        return _pdf_attachments(data, source_position=source_position, limits=bounds)
    result = _payload_source(
        data,
        media_type=media_type,
        filename=filename,
        source_position=source_position,
        carrier="file",
        attachment_name=None,
        limits=bounds,
    )
    return (result,) if result is not None else ()


def _pdf_attachments(data: bytes, *, source_position: int, limits: StructuredLimits) -> tuple[StructuredSource, ...]:
    try:
        import pypdfium2 as pdfium
        from pypdfium2 import raw as pdfium_c

        sources: list[StructuredSource] = []
        with pdfium.PdfDocument(data) as document:
            count = document.count_attachments()
            if count > limits.max_pdf_attachments:
                raise StructuredSourceError("PDF exceeds the attachment count limit.")
            for index in range(count):
                attachment = document.get_attachment(index)
                length = ctypes.c_ulong()
                pdfium_c.FPDFAttachment_GetFile(attachment.raw, None, 0, length)
                if length.value > limits.max_attachment_bytes:
                    raise StructuredSourceError("PDF attachment exceeds the byte limit.")
                payload = bytes(attachment.get_data())[: length.value]
                name = attachment.get_name()
                parsed = _payload_source(
                    payload,
                    media_type=_media_type(name),
                    filename=name,
                    source_position=source_position,
                    carrier="pdf_attachment",
                    attachment_name=name,
                    limits=limits,
                )
                if parsed is not None:
                    sources.append(parsed)
        return tuple(sources)
    except StructuredSourceError:
        raise
    except Exception as error:
        raise StructuredSourceError("PDF attachments could not be enumerated safely.") from error


def _payload_source(
    data: bytes,
    *,
    media_type: str,
    filename: str,
    source_position: int,
    carrier: StructuredCarrier,
    attachment_name: str | None,
    limits: StructuredLimits,
) -> StructuredSource | None:
    stripped = data.lstrip()
    lower_name = filename.lower()
    lower_media_type = media_type.lower()
    wrapped = lower_name.endswith((".p7m", ".zip", ".gz")) or lower_media_type in {
        "application/pkcs7-mime",
        "application/zip",
        "application/gzip",
    }
    if wrapped:
        kind, version, facts, reasons = (
            "unsupported_structured",
            None,
            [],
            ["Signed or compressed structured payload requires a bounded decoder and review."],
        )
    else:
        xml_declared = lower_media_type in {"application/xml", "text/xml"} or lower_name.endswith(".xml")
        bom_text = _decode_bom(data)
        signature = (bom_text if bom_text is not None else stripped[:128].decode("ascii", "ignore")).lstrip()
        edi_data = bom_text.encode("ascii") if bom_text is not None else data
        edi_declared = lower_media_type in {
            "application/edi",
            "application/edifact",
            "application/x12",
        } or lower_name.endswith((".edi", ".edifact", ".x12"))
        if signature.startswith("<") or xml_declared:
            kind, version, facts, reasons = _xml_facts(data, limits)
        elif signature.startswith(("UNA", "UNB")):
            kind, version, facts, reasons = _edifact_facts(edi_data, limits)
        elif signature.startswith("ISA"):
            kind, version, facts, reasons = _x12_facts(edi_data, limits)
        elif edi_declared:
            kind, version, facts, reasons = (
                "unsupported_structured",
                None,
                [],
                ["Declared EDI payload has an unsupported or malformed envelope and requires review."],
            )
        else:
            return None
    return StructuredSource(
        kind=kind,
        media_type=media_type or _media_type(filename),
        carrier=carrier,
        name=filename,
        content_sha256=hashlib.sha256(data).hexdigest(),
        facts=tuple(facts),
        evidence=StructuredEvidence(
            source_position=source_position,
            attachment_name=attachment_name,
            format_id=kind,
            standard_version=version,
            payload_size=len(data),
        ),
        review_reasons=tuple(reasons),
    )


def _xml_facts(data: bytes, limits: StructuredLimits):
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as error:
        raise StructuredSourceError("XML document type and entity declarations are not accepted.") from error
    except ElementTree.ParseError as error:
        raise StructuredSourceError("Structured XML is not well formed.") from error
    count = text_bytes = 0
    stack = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        count += 1
        text_bytes += len((element.text or "").encode("utf-8"))
        if count > limits.max_xml_elements or depth > limits.max_xml_depth or text_bytes > limits.max_xml_text_bytes:
            raise StructuredSourceError("Structured XML exceeds parsing limits.")
        stack.extend((child, depth + 1) for child in element)
    namespace, local = _qname(root.tag)
    if namespace == _UBL_NS and local == "Invoice":
        return "ubl_invoice", _attr_local(root, "UBLVersionID"), _ubl_facts(root), []
    if namespace == _CII_RSM_NS and local == _CII_ROOT:
        return "cii_cross_industry_invoice", _cii_version(root), _cii_facts(root), []
    return "unsupported_structured", None, [], [f"Unsupported XML root {{{namespace}}}{local} requires review."]


def _ubl_facts(root: ElementTree.Element) -> list[StructuredFact]:
    facts: list[StructuredFact] = []
    paths = {
        "invoice.reference": ("ID",),
        "invoice.issue_date": ("IssueDate",),
        "invoice.due_date": ("DueDate",),
        "invoice.currency": ("DocumentCurrencyCode",),
        "invoice.document_total": ("LegalMonetaryTotal", "PayableAmount"),
        "vendor.name": ("AccountingSupplierParty", "Party", "PartyName", "Name"),
        "vendor.tax_id": ("AccountingSupplierParty", "Party", "PartyTaxScheme", "CompanyID"),
        "vendor.email": ("AccountingSupplierParty", "Party", "Contact", "ElectronicMail"),
        "vendor.address.street": ("AccountingSupplierParty", "Party", "PostalAddress", "StreetName"),
        "vendor.address.extended": ("AccountingSupplierParty", "Party", "PostalAddress", "AdditionalStreetName"),
        "vendor.address.po_box": ("AccountingSupplierParty", "Party", "PostalAddress", "Postbox"),
        "vendor.address.city": ("AccountingSupplierParty", "Party", "PostalAddress", "CityName"),
        "vendor.address.region": ("AccountingSupplierParty", "Party", "PostalAddress", "CountrySubentity"),
        "vendor.address.postal_code": ("AccountingSupplierParty", "Party", "PostalAddress", "PostalZone"),
        "vendor.address.country": (
            "AccountingSupplierParty", "Party", "PostalAddress", "Country", "IdentificationCode"
        ),
        "bank.account_number": ("PaymentMeans", "PayeeFinancialAccount", "ID"),
        "bank.holder_name": ("PaymentMeans", "PayeeFinancialAccount", "Name"),
        "bank.bic": ("PaymentMeans", "PayeeFinancialAccount", "FinancialInstitutionBranch", "ID"),
        "bank.country": (
            "PaymentMeans",
            "PayeeFinancialAccount",
            "FinancialInstitutionBranch",
            "Address",
            "Country",
            "IdentificationCode",
        ),
    }
    for field, path in paths.items():
        _copy_first(facts, root, field, path)
    for occurrence, line in enumerate(_children_at(root, ("InvoiceLine",))):
        for field, path in (
            ("line.description", ("Item", "Description")),
            ("line.description", ("Item", "Name")),
            ("line.quantity", ("InvoicedQuantity",)),
            ("line.unit_price", ("Price", "PriceAmount")),
        ):
            if not any(f.field == field and f.occurrence == occurrence for f in facts):
                _copy_first(facts, line, field, path, occurrence=occurrence, prefix=f"InvoiceLine[{occurrence}]")
    return facts


def _cii_facts(root: ElementTree.Element) -> list[StructuredFact]:
    facts: list[StructuredFact] = []
    paths = {
        "invoice.reference": ("ExchangedDocument", "ID"),
        "invoice.issue_date": ("ExchangedDocument", "IssueDateTime", "DateTimeString"),
        "invoice.due_date": (
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradePaymentTerms",
            "DueDateDateTime",
            "DateTimeString",
        ),
        "invoice.currency": ("SupplyChainTradeTransaction", "ApplicableHeaderTradeSettlement", "InvoiceCurrencyCode"),
        "invoice.document_total": (
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementHeaderMonetarySummation",
            "GrandTotalAmount",
        ),
        "vendor.name": ("SupplyChainTradeTransaction", "ApplicableHeaderTradeAgreement", "SellerTradeParty", "Name"),
        "vendor.tax_id": (
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeAgreement",
            "SellerTradeParty",
            "SpecifiedTaxRegistration",
            "ID",
        ),
        "vendor.email": (
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeAgreement",
            "SellerTradeParty",
            "URIUniversalCommunication",
            "URIID",
        ),
        "vendor.address.street": (
            "SupplyChainTradeTransaction", "ApplicableHeaderTradeAgreement", "SellerTradeParty",
            "PostalTradeAddress", "LineOne",
        ),
        "vendor.address.extended": (
            "SupplyChainTradeTransaction", "ApplicableHeaderTradeAgreement", "SellerTradeParty",
            "PostalTradeAddress", "LineTwo",
        ),
        "vendor.address.city": (
            "SupplyChainTradeTransaction", "ApplicableHeaderTradeAgreement", "SellerTradeParty",
            "PostalTradeAddress", "CityName",
        ),
        "vendor.address.region": (
            "SupplyChainTradeTransaction", "ApplicableHeaderTradeAgreement", "SellerTradeParty",
            "PostalTradeAddress", "CountrySubDivisionName",
        ),
        "vendor.address.postal_code": (
            "SupplyChainTradeTransaction", "ApplicableHeaderTradeAgreement", "SellerTradeParty",
            "PostalTradeAddress", "PostcodeCode",
        ),
        "vendor.address.country": (
            "SupplyChainTradeTransaction", "ApplicableHeaderTradeAgreement", "SellerTradeParty",
            "PostalTradeAddress", "CountryID",
        ),
        "bank.account_number": (
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementPaymentMeans",
            "PayeePartyCreditorFinancialAccount",
            "IBANID",
        ),
        "bank.holder_name": (
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementPaymentMeans",
            "PayeePartyCreditorFinancialAccount",
            "AccountName",
        ),
        "bank.bic": (
            "SupplyChainTradeTransaction",
            "ApplicableHeaderTradeSettlement",
            "SpecifiedTradeSettlementPaymentMeans",
            "PayeeSpecifiedCreditorFinancialInstitution",
            "BICID",
        ),
    }
    for field, path in paths.items():
        _copy_first(facts, root, field, path)
    lines = [
        element for element in root.iter() if _qname(element.tag) == (_CII_RAM_NS, "IncludedSupplyChainTradeLineItem")
    ]
    for occurrence, line in enumerate(lines):
        for field, path in (
            ("line.description", ("SpecifiedTradeProduct", "Description")),
            ("line.description", ("SpecifiedTradeProduct", "Name")),
            ("line.quantity", ("SpecifiedLineTradeDelivery", "BilledQuantity")),
            ("line.unit_price", ("SpecifiedLineTradeAgreement", "NetPriceProductTradePrice", "ChargeAmount")),
        ):
            if not any(f.field == field and f.occurrence == occurrence for f in facts):
                _copy_first(
                    facts,
                    line,
                    field,
                    path,
                    occurrence=occurrence,
                    prefix=f"IncludedSupplyChainTradeLineItem[{occurrence}]",
                )
    return facts


def _edifact_facts(data: bytes, limits: StructuredLimits):
    text = _ascii(data)
    component, element, release, terminator = (":", "+", "?", "'")
    start = 0
    if text.startswith("UNA"):
        if len(text) < 9:
            raise StructuredSourceError("EDIFACT UNA segment is incomplete.")
        component, element, release, terminator = text[3], text[4], text[6], text[8]
        start = 9
    if release in text[start:]:
        return (
            "unsupported_structured",
            None,
            [],
            ["EDIFACT release-character escaping requires a full syntax decoder and review."],
        )
    segments = _split_escaped(text[start:], terminator, release, limits)
    parsed = [
        (parts[0], parts[1:])
        for segment in segments
        if (parts := _split_escaped(segment.strip(), element, release, limits))
    ]
    unb = next((values for tag, values in parsed if tag == "UNB"), None)
    messages = [values for tag, values in parsed if tag == "UNH"]
    unh = messages[0] if messages else None
    if unb is None or unh is None:
        raise StructuredSourceError("EDIFACT interchange lacks UNB or UNH.")
    message = unh[1].split(component) if len(unh) > 1 else []
    version = ":".join(message[1:4]) or None
    if not message or message[0] != "INVOIC":
        return "unsupported_structured", version, [], ["EDIFACT message is not INVOIC and requires review."]
    if len(messages) != 1:
        return "unsupported_structured", version, [], ["Multiple EDIFACT messages require separate review."]
    facts: list[StructuredFact] = []
    line = -1
    for index, (tag, values) in enumerate(parsed):
        path = f"{tag}[{index}]"
        if tag == "LIN":
            line += 1
        elif tag == "BGM" and len(values) > 1:
            _fact(facts, "invoice.reference", values[1].split(component)[0], f"{path}/C106/1004")
        elif tag == "DTM" and values:
            parts = values[0].split(component)
            if len(parts) > 1 and parts[0] in {"137", "13"}:
                _fact(
                    facts,
                    "invoice.issue_date" if parts[0] == "137" else "invoice.due_date",
                    parts[1],
                    f"{path}/C507/2380",
                )
        elif tag == "CUX" and values:
            parts = values[0].split(component)
            if len(parts) > 1 and parts[0] == "2":
                _fact(facts, "invoice.currency", parts[1], f"{path}/C504/6345")
        elif tag == "MOA" and values:
            parts = values[0].split(component)
            if len(parts) > 1 and parts[0] in {"9", "39"}:
                _fact(facts, "invoice.document_total", parts[1], f"{path}/C516/5004")
        elif tag == "NAD" and values and values[0] == "SU":
            if len(values) > 1:
                _fact(facts, "vendor.identifier", values[1].split(component)[0], f"{path}/C082/3039")
            if len(values) > 3:
                _fact(facts, "vendor.name", values[3].split(component)[0], f"{path}/C080/3036")
            if len(values) > 4:
                address_lines = values[4].split(component)
                _fact(facts, "vendor.address.street", address_lines[0], f"{path}/C059/3042[0]")
                if len(address_lines) > 1:
                    _fact(facts, "vendor.address.extended", address_lines[1], f"{path}/C059/3042[1]")
            for field, value_index, source in (
                ("vendor.address.city", 5, "3164"),
                ("vendor.address.region", 6, "C819/3229"),
                ("vendor.address.postal_code", 7, "3251"),
                ("vendor.address.country", 8, "3207"),
            ):
                if len(values) > value_index:
                    _fact(facts, field, values[value_index].split(component)[0], f"{path}/{source}")
        elif line >= 0 and tag in {"QTY", "PRI", "IMD"} and values:
            parts = values[0].split(component)
            if tag == "QTY" and len(parts) > 1 and parts[0] == "47":
                _fact(facts, "line.quantity", parts[1], f"{path}/C186/6060", line)
            elif tag == "PRI" and len(parts) > 1 and parts[0] in {"AAA", "AAB"}:
                _fact(facts, "line.unit_price", parts[1], f"{path}/C509/5118", line)
            elif tag == "IMD" and len(values) > 2:
                description = values[2].split(component)[-1]
                _fact(facts, "line.description", description, f"{path}/C273/7008", line)
    return "edifact_invoic", version, facts, []


def _x12_facts(data: bytes, limits: StructuredLimits):
    text = _ascii(data)
    if len(text) < 106:
        raise StructuredSourceError("X12 ISA segment is incomplete.")
    element, terminator = text[3], text[105]
    segments = _split_escaped(text, terminator, "", limits)
    parsed = [segment.strip().split(element) for segment in segments if segment.strip()]
    isa = parsed[0] if parsed else []
    transactions = [values for values in parsed if values[0] == "ST"]
    st = transactions[0] if transactions else None
    version = isa[12] if len(isa) > 12 else None
    if st is None:
        raise StructuredSourceError("X12 interchange lacks ST.")
    if len(st) < 2 or st[1] != "810":
        return "unsupported_structured", version, [], ["X12 transaction set is not 810 and requires review."]
    if len(transactions) != 1:
        return "unsupported_structured", version, [], ["Multiple X12 transaction sets require separate review."]
    facts: list[StructuredFact] = []
    line = -1
    for index, values in enumerate(parsed):
        tag, path = values[0], f"{values[0]}[{index}]"
        if tag == "IT1":
            line += 1
            if len(values) > 2:
                _fact(facts, "line.quantity", values[2], f"{path}/IT102", line)
            if len(values) > 4:
                _fact(facts, "line.unit_price", values[4], f"{path}/IT104", line)
        elif tag == "BIG":
            if len(values) > 1:
                _fact(facts, "invoice.issue_date", values[1], f"{path}/BIG01")
            if len(values) > 2:
                _fact(facts, "invoice.reference", values[2], f"{path}/BIG02")
        elif tag == "CUR" and len(values) > 2:
            _fact(facts, "invoice.currency", values[2], f"{path}/CUR02")
        elif tag == "N1" and len(values) > 2 and values[1] == "SE":
            _fact(facts, "vendor.name", values[2], f"{path}/N102")
            if len(values) > 4:
                _fact(facts, "vendor.identifier", values[4], f"{path}/N104")
        elif tag == "PID" and line >= 0 and len(values) > 5:
            _fact(facts, "line.description", values[5], f"{path}/PID05", line)
        elif tag == "TDS" and len(values) > 1:
            _fact(facts, "invoice.document_total", values[1], f"{path}/TDS01")
    reasons = (
        ["X12 TDS total is retained as a raw integer with profile-defined implied decimal scale."]
        if any(v[0] == "TDS" for v in parsed)
        else []
    )
    return "x12_810", version, facts, reasons


def _copy_first(facts, root, field, path, *, occurrence=0, prefix=""):
    elements = _children_at(root, path)
    if elements:
        value = "".join(elements[0].itertext()).strip()
        _fact(facts, field, value, "/".join(filter(None, (prefix, *path))), occurrence)


def _children_at(root, path):
    current = [root]
    for name in path:
        current = [
            child
            for parent in current
            for child in parent
            if _qname(child.tag)[1] == name and _qname(child.tag)[0] in _STANDARD_NAMESPACES
        ]
    return current


def _fact(facts, field, value, path, occurrence=0):
    value = value.strip()
    if value:
        facts.append(StructuredFact(field, value, path, occurrence))


def _qname(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _attr_local(root, name):
    element = next(
        (e for e in root if _qname(e.tag)[1] == name and _qname(e.tag)[0] in _STANDARD_NAMESPACES),
        None,
    )
    return "".join(element.itertext()).strip() if element is not None else None


def _cii_version(root):
    namespace, _ = _qname(root.tag)
    return namespace.rsplit(":", 1)[-1] if namespace else None


def _ascii(data: bytes) -> str:
    try:
        return data.decode("ascii")
    except UnicodeDecodeError as error:
        raise StructuredSourceError("EDI payload must be ASCII.") from error


def _decode_bom(data: bytes) -> str | None:
    for marker, encoding in (
        (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xff\xfe\x00\x00", "utf-32"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(marker):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError as error:
                raise StructuredSourceError("Declared text encoding is invalid.") from error
    return None


def _split_escaped(text: str, separator: str, release: str, limits: StructuredLimits) -> list[str]:
    values, current = [], []
    index = 0
    while index < len(text):
        char = text[index]
        if release and char == release and index + 1 < len(text):
            following = text[index + 1]
            if following in {separator, release}:
                current.append(following)
                index += 2
                continue
            current.append(char)
        elif char == separator:
            value = "".join(current)
            if len(value) > limits.max_edi_segment_length:
                raise StructuredSourceError("EDI segment exceeds the length limit.")
            values.append(value)
            if len(values) > limits.max_edi_segments:
                raise StructuredSourceError("EDI exceeds the segment count limit.")
            current = []
        else:
            current.append(char)
        index += 1
    if current:
        values.append("".join(current))
    return values


def _media_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".xml"):
        return "application/xml"
    if lower.endswith((".edi", ".edifact", ".x12")):
        return "application/edi"
    return "application/octet-stream"
