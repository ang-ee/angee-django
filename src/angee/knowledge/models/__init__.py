"""Knowledge model declarations for Django app population."""

from angee.knowledge.models.bindings import RecordBinding
from angee.knowledge.models.links import Link
from angee.knowledge.models.pages import MarkdownPage, Page
from angee.knowledge.models.vaults import Vault

__all__ = ["Link", "MarkdownPage", "Page", "RecordBinding", "Vault"]
