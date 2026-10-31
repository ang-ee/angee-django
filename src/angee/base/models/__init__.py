"""Native discovery entrypoint for foundation model declarations.

The foundation currently provides only abstract capabilities. Addons import
those capabilities from their declaring modules. Concrete or composed source
declarations added to this app must be imported here for native discovery.
"""

from angee.base.models.scope import DefaultedScopeMixin, NullableScopeMixin

__all__ = ["DefaultedScopeMixin", "NullableScopeMixin"]
