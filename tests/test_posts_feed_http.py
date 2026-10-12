"""Shared feed HTTP classification through a fake provider's predicates."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import pytest

from angee.integrate.errors import IntegrationError, RequestNotSentError
from angee.integrate.http import ResponseTooLargeError
from angee.integrate.net import AddressNotAllowedError
from angee.integrate.streams import CursorInvalid, SemanticError
from angee.messaging.backends import DeliveryUncertain
from angee.posts.backends import FeedBackend

URL = "https://feed.example/replies"


@pytest.fixture
def provider() -> FeedBackend:
    backend = FeedBackend(SimpleNamespace(config={}))
    backend.http = Mock()
    return backend


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("error", [
    OSError("transport"), httpx2.ReadTimeout("transport"), httpx2.WriteError("transport"),
    ResponseTooLargeError("oversized"),
])
def test_transport_outcome_depends_on_method(provider, method, error):
    provider.http.request.side_effect = error
    transient, refusal = Mock(), Mock()
    with pytest.raises(DeliveryUncertain if method == "POST" else IntegrationError) as raised:
        provider.request_json(method, URL, is_transient=transient, refusal=refusal)
    assert raised.value.transient is (method == "GET")
    transient.assert_not_called()
    refusal.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_ssrf_refusal_is_a_permanent_refusal_never_uncertain(provider, method):
    provider.http.request.side_effect = AddressNotAllowedError()
    with pytest.raises(IntegrationError) as raised:
        provider.request_json(method, URL, is_transient=Mock(), refusal=Mock())
    assert not raised.value.transient
    assert not isinstance(raised.value, DeliveryUncertain)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_not_sent_error_is_never_uncertain(provider, method):
    error = RequestNotSentError()
    provider.http.request.side_effect = error
    with pytest.raises(RequestNotSentError) as raised:
        provider.request_json(method, URL, is_transient=Mock(), refusal=Mock())
    assert raised.value is error and raised.value.transient


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status,content,marker", [
    (503, b"{}", False), (200, b"{", False), (503, b"unreadable", False),
    (200, b"[]", False), (200, b"null", False), (403, b'{"error":{}}', True),
])
def test_uncertain_http_outcomes_precede_refusals(provider, method, status, content, marker):
    response = httpx2.Response(status, content=content, headers={"Retry-After": "45"})
    provider.http.request.return_value = response
    transient = Mock(return_value=marker)
    refusal = Mock(return_value=IntegrationError("Definite rejection."))
    with pytest.raises(DeliveryUncertain if method == "POST" else IntegrationError) as raised:
        provider.request_json(method, URL, is_transient=transient, refusal=refusal)
    assert raised.value.transient is (method == "GET")
    assert raised.value.retry_after == (timedelta(seconds=45) if method == "GET" else None)
    refusal.assert_not_called()
    if content in (b"{}", b'{"error":{}}'):
        transient.assert_called_once_with(response, response.json())
    else:
        transient.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("error", [
    IntegrationError("Authorization refused."),
    IntegrationError("Quota refused.", retry_after=timedelta(hours=2)),
    CursorInvalid(), SemanticError("record_unavailable"),
])
def test_provider_refusals_retain_their_native_contract(provider, method, error):
    provider.http.request.return_value = httpx2.Response(403, json={"error": "refused"})
    with pytest.raises(type(error)) as raised:
        provider.request_json(method, URL, is_transient=lambda response, data: False,
                              refusal=lambda response, data: error)
    assert raised.value is error


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unhandled_http_refusal_is_permanent(provider, method):
    provider.http.request.return_value = httpx2.Response(404, json={})
    with pytest.raises(IntegrationError) as raised:
        provider.request_json(method, URL, is_transient=lambda response, data: False,
                              refusal=lambda response, data: None)
    assert not raised.value.transient
    assert not isinstance(raised.value, DeliveryUncertain)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unreadable_client_error_is_classified_by_the_predicates_never_uncertain(provider, method):
    response = httpx2.Response(400, content=b"<html>bad request</html>")
    provider.http.request.return_value = response
    transient = Mock(return_value=False)
    refusal = Mock(return_value=None)
    with pytest.raises(IntegrationError) as raised:
        provider.request_json(method, URL, is_transient=transient, refusal=refusal)
    assert not raised.value.transient
    assert not isinstance(raised.value, DeliveryUncertain)
    transient.assert_called_once_with(response, {})
    refusal.assert_called_once_with(response, {})


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_success_forwards_native_request_arguments_and_returns_data(provider, method):
    provider.http.request.return_value = httpx2.Response(200, json={"id": "reply"})
    result = provider.request_json(method.lower(), URL, headers={"Accept": "application/json"},
                                   body=b"body", timeout=10, max_bytes=1024,
                                   is_transient=lambda response, data: False,
                                   refusal=lambda response, data: None)
    assert result == {"id": "reply"}
    provider.http.request.assert_called_once_with(method, URL, headers={"Accept": "application/json"},
                                                  body=b"body", timeout=10, max_bytes=1024)


def test_usage_delay_composes_retry_after_and_receives_provider_headers(provider):
    response = httpx2.Response(503, json={}, headers={"Retry-After": "45", "X-Usage-Delay": "120"})
    provider.http.request.return_value = response

    def delay(response):
        return max(provider.retry_after(response), timedelta(seconds=int(response.headers["X-Usage-Delay"])))

    with pytest.raises(IntegrationError) as raised:
        provider.request_json("GET", URL, is_transient=lambda response, data: False,
                              refusal=lambda response, data: None, retry_delay=delay)
    assert raised.value.retry_after == timedelta(seconds=120)


def test_overridden_retry_after_is_the_default_delay(provider, monkeypatch):
    provider.http.request.return_value = httpx2.Response(503, json={})
    monkeypatch.setattr(provider, "retry_after", lambda response: timedelta(hours=1))
    with pytest.raises(IntegrationError) as raised:
        provider.request_json("GET", URL, is_transient=lambda response, data: False,
                              refusal=lambda response, data: None)
    assert raised.value.retry_after == timedelta(hours=1)


def test_unsupported_method_is_refused_before_transport(provider):
    with pytest.raises(ValueError):
        provider.request_json("DELETE", URL, is_transient=Mock(), refusal=Mock())
    provider.http.request.assert_not_called()


@pytest.mark.parametrize("predicate", ["is_transient", "refusal"])
def test_predicate_exceptions_propagate(provider, predicate):
    provider.http.request.return_value = httpx2.Response(200, json={})
    error = SemanticError("provider_page_invalid")
    hooks = {"is_transient": Mock(return_value=False), "refusal": Mock(return_value=None)}
    hooks[predicate].side_effect = error
    with pytest.raises(SemanticError) as raised:
        provider.request_json("GET", URL, **hooks)
    assert raised.value is error
