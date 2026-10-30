"""Connect one probed CardDAV account through Parties and Integrate owners."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context


def connect_directory(user: Any, *, name: str, server_url: str, username: str, password: str) -> Any:
    """Probe unsaved credentials before atomically retaining the connected account."""

    Directory = apps.get_model("parties", "Directory")
    Handle = apps.get_model("parties", "Handle")
    credential_model = apps.get_model("integrate", "Credential")
    vendor_model = apps.get_model("integrate", "Vendor")
    credential_name = f"CardDAV — {name}"
    credential_material = {"username": username, "password": password}
    directory_values = {
        "owner": user,
        "backend_class": "carddav",
        "display_name": name,
        "config": {"server_url": server_url},
        "lifecycle": "connected",
        "created_by_id": user.pk,
    }

    # Complete validation and network discovery before opening the database
    # transaction. Both rows are intentionally unsaved probe inputs.
    probe_credential = credential_model.objects.prepare_local_credential(
        user,
        kind="basic_auth",
        name=credential_name,
        material=credential_material,
    )
    Directory(credential=probe_credential, **directory_values).backend.probe()

    # Persist every owned row in one write-only transaction after the probe.
    with system_context(reason="parties.graphql.connect_carddav"), transaction.atomic():
        credential = credential_model.objects.create_local_credential(
            user,
            kind="basic_auth",
            name=credential_name,
            material=credential_material,
        )
        vendor, _created = vendor_model.objects.get_or_create(slug="carddav", defaults={"display_name": "CardDAV"})
        directory = Directory.objects.create(
            vendor=vendor,
            credential=credential,
            **directory_values,
        )
        # The connected account's own login address is the admin's own handle:
        # claim it (control + normal person identity statement). Synced
        # *contacts'* handles get neither fact — only this connected account's.
        if username.strip():
            Handle.objects.claim_control(
                user,
                platform=Handle.Platform.for_value(username),
                value=username.strip(),
                asserted_by=user,
                evidence=[directory],
                display_name=name,
            )
    return directory
