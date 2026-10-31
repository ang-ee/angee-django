"""Native aggregate factory for external storage mounts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils.text import slugify
from rebac import system_context

from angee.base.implementations import resolve_impl_class
from angee.integrate.choices import IntegrationLifecycle
from angee.integrate.managers import IntegrationManager
from angee.storage_integrate.mounts import LocalFolderMountBackend, MountBackend, MountBrowseResult, MountLocation
from angee.storage_integrate.querysets import MountQuerySet

if TYPE_CHECKING:
    from angee.storage_integrate.models import Mount, MountMode

_LOCAL_VENDOR_SLUG = "local"
_MountManagerBase = IntegrationManager.from_queryset(MountQuerySet)


class MountManager[MountT: Mount](_MountManagerBase[MountT]):
    """Own atomic mount, drive and reference-backend provisioning."""

    def browse_source(
        self,
        backend_class: str,
        *,
        credential: Any | None = None,
        token: str = "",
    ) -> MountBrowseResult:
        """Dispatch pre-connection browsing through the configured Mount backend."""

        try:
            impl = resolve_impl_class(MountBackend, backend_class)
        except ImproperlyConfigured as error:
            raise ValidationError({"backend_class": str(error)}) from error
        listing = cast(type[MountBackend], impl).browse(credential=credential, token=token)
        if not issubclass(impl, LocalFolderMountBackend):
            return listing
        locations = (listing.location, *listing.entries)
        with system_context(reason="storage_integrate.browse_mount_source"):
            mounted = set(
                self.for_roots(location.token for location in locations).values_list("config__root", flat=True)
            )

        def admitted(location: MountLocation) -> MountLocation:
            if location.token in mounted and not location.blocked_reason:
                return replace(location, is_mountable=False, blocked_reason="Already mounted")
            return location

        return replace(listing, location=admitted(listing.location), entries=tuple(map(admitted, listing.entries)))

    def provision(
        self,
        user: Any,
        *,
        name: str,
        root: Path,
        mode: MountMode | str,
        backend_class: str,
        already_mounted_message: str,
        slug_default: str,
        reason: str,
    ) -> Any:
        """Provision and eagerly queue one pre-validated external-source Mount."""

        display_name = str(name).strip()
        if not display_name:
            raise ValidationError({"name": "A mount name is required."})
        try:
            mount_mode = self.model.Mode(str(getattr(mode, "value", mode)).strip().lower())
        except ValueError as error:
            raise ValidationError({"mode": f"Unsupported mount mode: {mode}"}) from error

        backend_model = apps.get_model("storage", "Backend")
        drive_model = apps.get_model("storage", "Drive")
        mount_model = self.model
        mount_config = dict(root=str(root))

        storage_backend_spec: tuple[str, dict[str, Any]] | None = None
        if mount_mode == self.model.Mode.REFERENCE:
            mount_backend = mount_model(
                backend_class=backend_class,
                config=mount_config,
            ).backend
            storage_backend_spec = mount_backend.storage_backend_spec()

        with system_context(reason=reason), transaction.atomic():
            if self.for_roots((str(root),)).exists():
                raise ValidationError({"path": already_mounted_message})
            slug = self._available_mount_slug(
                display_name,
                slug_default=slug_default,
                backend_model=backend_model,
                drive_model=drive_model,
            )
            drive_slug = f"mount-{slug}"
            if mount_mode == self.model.Mode.REFERENCE:
                if storage_backend_spec is None:
                    raise AssertionError("Reference Mount storage backend spec was not resolved.")
                storage_backend_key, storage_backend_config = storage_backend_spec
                storage_backend = backend_model.objects.create(
                    slug=drive_slug,
                    label=f"{display_name} (external)",
                    backend_class=storage_backend_key,
                    backend_config=storage_backend_config,
                    created_by_id=user.pk,
                )
                prefix = ""
            else:
                default = drive_model.objects.configured_default()
                if default is None:
                    raise ImproperlyConfigured("The configured default storage drive is missing.")
                storage_backend = default.backend
                prefix = f"mounts/{slug}"
            drive = drive_model.objects.create(
                backend_id=storage_backend.pk,
                slug=drive_slug,
                name=display_name,
                prefix=prefix,
                owner_id=user.pk,
                created_by_id=user.pk,
            )
            mount = mount_model.objects.create(
                vendor_id=apps.get_model("integrate", "Vendor").objects.seeded(_LOCAL_VENDOR_SLUG).pk,
                owner_id=user.pk,
                display_name=display_name,
                backend_class=backend_class,
                drive_id=drive.pk,
                mode=mount_mode,
                lifecycle=IntegrationLifecycle.DISCONNECTED,
                config=mount_config,
                created_by_id=user.pk,
            )
            mount.connect()
        mount.request_sync()
        return mount

    @staticmethod
    def _available_mount_slug(name: str, *, slug_default: str, backend_model: Any, drive_model: Any) -> str:
        """Return a slug whose mount-prefixed backend and drive ids are unused."""

        base = slugify(name) or slug_default
        suffix = 1
        candidate = base
        while (
            backend_model.objects.filter(slug=f"mount-{candidate}").exists()
            or drive_model.objects.filter(slug=f"mount-{candidate}").exists()
        ):
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate
