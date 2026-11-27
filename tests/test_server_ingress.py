"""Anonymous ingress preserves its IP identity and failure envelopes."""

import json

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from angee.base.serialization import MAX_JSON_DOCUMENT_DEPTH
from angee.server import ingress


def test_ingress_limits_each_ip_and_preserves_retry_after() -> None:
    """An exhausted IP cannot block a second IP's first request."""

    policy = ingress.AnonymousIngressPolicy(
        max_body_bytes=1024, allowed_fields=frozenset({"text"}),
        rate_limit=ingress.AnonymousIngressRateLimit(scope="test", burst=1, window_seconds=60),
    )
    factory = RequestFactory()
    cache.clear()
    try:
        first = factory.post("/", data={"text": "hello"}, content_type="application/json", REMOTE_ADDR="192.0.2.1")
        assert ingress.guard_anonymous_ingress(first, policy).payload == {"text": "hello"}
        with pytest.raises(ingress.AnonymousIngressRateLimited) as refused:
            ingress.guard_anonymous_ingress(first, policy)
        assert refused.value.retry_after == 60
        second = factory.post("/", data={"text": "hello"}, content_type="application/json", REMOTE_ADDR="192.0.2.2")
        assert ingress.guard_anonymous_ingress(second, policy).payload == {"text": "hello"}
    finally:
        cache.clear()


def test_ingress_cache_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native cache failures retain ingress's 503 rejection contract."""

    def fail(*args: object, **kwargs: object) -> int:
        raise OSError("cache unavailable")

    monkeypatch.setattr(ingress, "increment_window", fail)
    policy = ingress.AnonymousIngressPolicy(
        max_body_bytes=1024, allowed_fields=frozenset({"text"}),
        rate_limit=ingress.AnonymousIngressRateLimit(scope="test", burst=1, window_seconds=60),
    )
    request = RequestFactory().post("/", data={"text": "hello"}, content_type="application/json")
    with pytest.raises(ingress.AnonymousIngressUnavailable) as refused:
        ingress.guard_anonymous_ingress(request, policy)
    assert refused.value.status_code == 503


def test_ingress_refuses_a_body_nested_past_the_document_bound_as_malformed() -> None:
    """A body a level past ``MAX_JSON_DOCUMENT_DEPTH`` is malformed JSON, a client error; one at the bound is read."""

    def body(depth: int) -> str:
        levels = depth - 1
        return '{"text": ' + "[" * levels + "0" + "]" * levels + "}"

    policy = ingress.AnonymousIngressPolicy(
        max_body_bytes=1024, allowed_fields=frozenset({"text"}),
        rate_limit=ingress.AnonymousIngressRateLimit(scope="test", burst=2, window_seconds=60),
    )
    factory = RequestFactory()
    cache.clear()
    try:
        admitted = ingress.guard_anonymous_ingress(
            factory.post("/", data=body(MAX_JSON_DOCUMENT_DEPTH), content_type="application/json"), policy,
        )
        with pytest.raises(ingress.AnonymousIngressRejected) as refused:
            ingress.guard_anonymous_ingress(
                factory.post("/", data=body(MAX_JSON_DOCUMENT_DEPTH + 1), content_type="application/json"), policy,
            )
    finally:
        cache.clear()
    assert admitted.payload == json.loads(body(MAX_JSON_DOCUMENT_DEPTH))
    assert type(refused.value) is ingress.AnonymousIngressRejected
    assert refused.value.status_code == 400
    assert str(refused.value) == "Request body must be a valid UTF-8 JSON object."
