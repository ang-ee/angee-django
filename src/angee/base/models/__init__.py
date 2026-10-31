"""Foundation model package, inert outside Django's model-loading phase.

Addons import abstract capabilities from their declaring modules. BaseConfig's
native import_models hook exposes the addressable scope contracts for compiler
discovery after Django starts loading models.
"""
