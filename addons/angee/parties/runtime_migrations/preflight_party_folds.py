"""Name every row the parties folds or retirements would refuse before anything destructive runs.

The folds are proven-or-blocked, and the integrate and provenance retirements that
precede them in the chain are irreversible. This declaration evaluates both folds'
classification (``fold_party_handle_statements.plan`` and
``fold_party_aspects.plan``) against the pre-upgrade state and fails with the
complete list of blocking rows (model, pk, reason) when any exists. The folds keep
their own guards as defence in depth; this is where a blocker is discovered first.
It also prints what the folds will leave unasserted (takeout imports, values with
no recorded person actor, service-account decisions attributed to the fold), so the
operator sees the counts before deciding to proceed.

Ordering is the composer's: the guard holds whenever legacy carriers are in the
state, so the declaration is planned in the first round of the chain, and every
destructive declaration waits for a schema that only a later round produces, which
makes it depend on this node (``angee/compose/migrations.py`` attaches later-round
declarations to every leaf planned so far). It reads legacy tables only, so it
needs none of the chain's schema and never writes. Reverse is a no-op.

This body composes the fold modules' predicates instead of copying them, so the
preflight and the folds cannot disagree; those modules are append-only history.
"""

from collections import Counter

from django.conf import settings
from django.db import migrations

from angee.parties.runtime_migrations import fold_party_aspects, fold_party_handle_statements, retire_party_carriers


def applies(state):
    return ("parties", "partyhandle") in state.models


def blockers(apps, db):
    """Return (blocking rows, notices) for both parties folds on this state."""
    _, _, handle_blockers, notices = fold_party_handle_statements.plan(apps, db)
    _, _, _, aspect_blockers, aspect_notices = fold_party_aspects.plan(apps, db)
    notices.update(aspect_notices)
    # The carrier retirement's pre-fold checks: enrollment identity, link retention,
    # extension data, MergeVeto references. Its post-fold parity proves the folds' own
    # output and cannot refuse rows the folds accepted.
    retirement_problems, retirement_notices = retire_party_carriers.plan(apps, db, folded=False)
    notices.update(Counter(retirement_notices))
    return [*handle_blockers, *aspect_blockers, *retirement_problems], notices


def forwards(apps, schema_editor):
    rows, notices = blockers(apps, schema_editor.connection.alias)
    for reason, number in sorted(notices.items()):
        print(f"Parties fold preflight: {number} x {reason}")
    if rows:
        raise ValueError(
            f"Parties fold preflight: {len(rows)} rows block this upgrade; attribute or remove them, "
            "nothing has been dropped:\n" + "\n".join(f"{row.label} {row.pk}: {row.reason}" for row in rows)
        )
    print("Parties fold preflight: no blocking rows.")


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    ("contenttypes", "0002_remove_content_type_name")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
