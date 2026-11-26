"""Anonymous ingress preserves its IP identity and failure envelopes."""

import pytest
from django.core.cache import cache
from django.test import RequestFactory

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


def test_ingress_refuses_a_body_nested_past_the_json_decoder_as_malformed() -> None:
    """A body too deep for the decoder's stack is malformed JSON, answered as a client error."""

    policy = ingress.AnonymousIngressPolicy(
        max_body_bytes=400_000, allowed_fields=frozenset({"text"}),
        rate_limit=ingress.AnonymousIngressRateLimit(scope="test", burst=1, window_seconds=60),
    )
    body = '{"text": ' + "[" * 100_000 + "]" * 100_000 + "}"
    request = RequestFactory().post("/", data=body, content_type="application/json")
    cache.clear()
    try:
        with pytest.raises(ingress.AnonymousIngressRejected) as refused:
            ingress.guard_anonymous_ingress(request, policy)
    finally:
        cache.clear()
    assert refused.value.status_code == 400
