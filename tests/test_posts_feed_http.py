"""Shared feed HTTP classification through a fake provider's predicates."""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, Mock

import httpx2
import pytest

from angee.integrate.errors import IntegrationError, RequestNotBuiltError, RequestNotSentError
from angee.integrate.net import AddressNotAllowedError
from angee.integrate.streams import CursorInvalid, SemanticError
from angee.integrate.testing.http import mock_transport
from angee.messaging.backends import DeliveryUncertain
from angee.posts.backends import FeedBackend

URL = "https://feed.example/replies"


@pytest.fixture
def provider() -> FeedBackend:
    backend = FeedBackend(SimpleNamespace(config={}))
    backend.http = Mock()
    return backend


def _feed() -> FeedBackend:
    """A feed adapter over the real pinned client, so integrate's refusal hook runs."""

    return FeedBackend(SimpleNamespace(config={}))


def _answer(answer: httpx2.Response | Exception) -> Any:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    return respond


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("error", [
    httpx2.ReadTimeout("transport"), httpx2.WriteError("transport"), httpx2.RemoteProtocolError("transport"),
])
def test_transport_outcome_depends_on_method(method, error):
    """Integrate classifies the failure (``HttpRefusal``); a write that may have been sent is uncertain."""

    transient, refusal = Mock(), Mock()
    with mock_transport(_answer(error)), pytest.raises(IntegrationError) as raised:
        _feed().request_json(method, URL, is_transient=transient, refusal=refusal)
    if method == "POST":
        assert isinstance(raised.value, DeliveryUncertain)
    else:
        assert not isinstance(raised.value, DeliveryUncertain)
        assert (raised.value.transient, raised.value.retry_after) == (True, None)
        assert raised.value.__cause__ is error
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
@pytest.mark.parametrize("error", [RequestNotSentError(), RequestNotBuiltError()], ids=["not-connected", "not-built"])
def test_not_sent_error_is_never_uncertain(method, error):
    """Nothing was written: integrate's not-sent failure is raised unchanged, its own transience with it."""

    with mock_transport(_answer(error)), pytest.raises(RequestNotSentError) as raised:
        _feed().request_json(method, URL, is_transient=Mock(), refusal=Mock())
    assert raised.value is error and raised.value.transient is error.transient


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(("content", "cause"), [
    pytest.param(b"x" * 65, "oversized", id="oversized"),
    pytest.param(b"{", "unreadable", id="not-json"),
    pytest.param(b"[]", "unreadable", id="not-an-object"),
    pytest.param(b"null", "unreadable", id="null"),
])
def test_a_reads_oversized_or_unreadable_answer_is_permanent_and_a_writes_is_uncertain(method, content, cause):
    """Integrate's rule for a read: the same request brings the same answer. A write may have taken effect."""

    transient, refusal = Mock(), Mock()
    with mock_transport(lambda request: httpx2.Response(200, content=content, headers={"Retry-After": "45"})):
        with pytest.raises(IntegrationError) as raised:
            FeedBackend(SimpleNamespace(config={})).request_json(
                method, URL, max_bytes=64, is_transient=transient, refusal=refusal,
            )

    if method == "POST":
        assert isinstance(raised.value, DeliveryUncertain)
    else:
        assert not isinstance(raised.value, DeliveryUncertain)
        assert (raised.value.transient, raised.value.retry_after, str(raised.value)) == (
            False, None, f"The feed returned an {cause} response.",
        )
    transient.assert_not_called()
    refusal.assert_not_called()


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("status,content,marker", [
    (503, b"{}", False), (503, b"unreadable", False), (403, b'{"error":{}}', True),
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
    transient.assert_called_once_with(response, {} if content == b"unreadable" else response.json())


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
@pytest.mark.parametrize(("status", "transient", "wait", "message"), [
    pytest.param(429, True, timedelta(seconds=45), "The feed is rate limiting requests (HTTP 429).",
                 id="a-rate-limit-waits"),
    pytest.param(404, False, None, "The feed refused the request (HTTP 404).", id="a-refusal-is-permanent"),
])
def test_an_answer_the_adapter_does_not_name_is_integrates_refusal(method, status, transient, wait, message):
    """Integrate's status rule and wait decide what the adapter's predicates leave; a 4xx is never uncertain."""

    answer = httpx2.Response(status, json={"error": "refused"}, headers={"Retry-After": "45"} if wait else {})
    with mock_transport(_answer(answer)), pytest.raises(IntegrationError) as raised:
        _feed().request_json(method, URL, is_transient=lambda response, data: False,
                             refusal=lambda response, data: None)
    assert not isinstance(raised.value, DeliveryUncertain)
    assert (raised.value.transient, raised.value.retry_after, str(raised.value)) == (transient, wait, message)


@pytest.mark.parametrize("content", [b'{"id": "reply"}', b"<html>moved</html>"], ids=["json", "html"])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_redirect_is_no_answer_a_read_refused_and_a_write_uncertain(method, content):
    """A 3xx the request did not follow is not a result: a read's refusal, and a write's may have taken effect."""

    answer = httpx2.Response(303, content=content, headers={"Location": "https://feed.example/replies/1"})
    with mock_transport(_answer(answer)), pytest.raises(IntegrationError) as raised:
        _feed().request_json(method, URL, is_transient=lambda response, data: False,
                             refusal=lambda response, data: None)
    if method == "POST":
        assert isinstance(raised.value, DeliveryUncertain)
    else:
        assert (raised.value.transient, str(raised.value)) == (False, "The feed refused the request (HTTP 303).")


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_success_httpx2_cannot_decode_is_a_reads_unreadable_answer_and_a_writes_uncertain(method):
    answer = httpx2.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx2.ByteStream(b"not gzip"))
    with mock_transport(_answer(answer)), pytest.raises(IntegrationError) as raised:
        _feed().request_json(method, URL, is_transient=Mock(), refusal=Mock())
    if method == "POST":
        assert isinstance(raised.value, DeliveryUncertain)
    else:
        assert (raised.value.transient, str(raised.value)) == (False, "The feed returned an unreadable response.")
        assert isinstance(raised.value.__cause__, httpx2.DecodingError)


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
                                                  body=b"body", timeout=10, max_bytes=1024, error=ANY)


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
