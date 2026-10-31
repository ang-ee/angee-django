"""Historical public imports for the foundation advisory-lock owner.

New callers import :mod:`angee.base.locks`; background delivery has no private
lock backend or state.
"""

from angee.base.locks import (
    LocalLockBackend as LocalLockBackend,
)
from angee.base.locks import (
    LockBackend as LockBackend,
)
from angee.base.locks import (
    LockHandle as LockHandle,
)
from angee.base.locks import (
    LockKey as LockKey,
)
from angee.base.locks import (
    PostgresAdvisoryLockBackend as PostgresAdvisoryLockBackend,
)
from angee.base.locks import (
    get_lock_backend as get_lock_backend,
)
from angee.base.locks import (
    record_lock_key as record_lock_key,
)
from angee.base.locks import (
    task_lock as task_lock,
)
from angee.base.locks import (
    task_lock_is_held as task_lock_is_held,
)
from angee.base.locks import (
    task_locks_are_cross_process as task_locks_are_cross_process,
)
