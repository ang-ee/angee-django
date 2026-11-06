from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

from angee.base.serialization import canonical_json
from angee.provenance.engine.serialization import json_safe, restore_json


def test_native_values_roundtrip_without_tag_collisions():
    values = {
        "date": date(2020, 1, 2),
        "datetime": datetime(2020, 1, 2, tzinfo=UTC),
        "time": time(3, 4),
        "duration": timedelta(days=-2, microseconds=17),
        "decimal": Decimal("1.250"),
        "uuid": uuid4(),
        "binary": b"\x00\xff",
        "json": {"$type": "Decimal", "value": "not-a-number"},
    }
    encoded = json_safe(values)
    assert restore_json(encoded) == values
    assert canonical_json(encoded) == canonical_json(json_safe(dict(reversed(list(values.items())))))
