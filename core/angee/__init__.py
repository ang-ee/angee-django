"""Host and namespace for composed Angee applications."""

from pkgutil import extend_path

# Framework addons and consumer addons share the ``angee`` package across their
# configured source roots. The core wheel must preserve that package
# composition instead of hiding the addon portions behind this regular package.
__path__ = extend_path(__path__, __name__)
