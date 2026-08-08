"""Standard-library adapter for OpenAI-compatible Chat Completions APIs.

The adapter deliberately has a small request surface.  The runner controls the
prompt, sampling temperature, output-token cap, and optional call seed; static
provider extensions cannot replace those experimental fields.  HTTP is
injected behind :class:`HTTPTransport`, so the adapter can be tested without a
network or a provider SDK.

Credentials are used only to construct the Authorization header.  They are
wrapped in a redacting value, excluded from representations and errors, and are
never placed in request JSON or returned provider envelopes.
"""

from __future__ import annotations

import json
import math
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ..experiment import GeneratorContext
from ..runner import (
    CANDIDATE_FORMATS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    GenerationResponse,
)


_PROTECTED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "max_tokens",
        "response_format",
        "seed",
    }
)

# This value must remain outside the DSL grammar. It represents a paid model
# response whose assistant content did not obey the frozen JSON candidate
# schema. Raw malformed content is deliberately not copied into the adapter's
# canonical expression field, where a list could otherwise be mistaken for a
# valid tuple-like AST by downstream compatibility parsers.
_INVALID_CANDIDATE_SENTINEL = "__INVALID_JSON_CANDIDATE_SCHEMA__"


class OpenAICompatibleError(RuntimeError):
    """Base class for safe, normalized provider-adapter failures."""


class TransportError(OpenAICompatibleError):
    """Safe transport failure with a closed recovery classification.

    ``delivery_ambiguous`` is deliberately conservative.  A true value means
    the caller cannot prove that the provider did not receive the request; it
    may permit abandoning and restarting an entire predeclared transaction,
    but never retrying the individual logical slot.  A false value identifies
    a local transport/configuration contract failure and is campaign-fatal.
    Raw exception text is never retained.
    """

    __slots__ = ("category", "delivery_ambiguous")

    _CATEGORIES = frozenset(
        {
            "timeout",
            "dns",
            "tls",
            "connection_refused",
            "connection_reset",
            "network_io",
            "injected_transport_exception",
            "local_request_configuration",
            "local_transport_contract",
        }
    )

    def __init__(
        self,
        message: str = "chat completions HTTP transport failed",
        *,
        category: str = "network_io",
        delivery_ambiguous: bool = True,
    ) -> None:
        if category not in self._CATEGORIES:
            raise ValueError("transport failure category is not in the closed set")
        if type(delivery_ambiguous) is not bool:
            raise TypeError("delivery_ambiguous must be a boolean")
        self.category = category
        self.delivery_ambiguous = delivery_ambiguous
        super().__init__(message)

    @property
    def recovery_scope(self) -> str:
        return "restart_whole_shard" if self.delivery_ambiguous else "campaign_fatal"


class HTTPStatusError(OpenAICompatibleError):
    """The provider returned a non-successful HTTP status."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"chat completions request failed with HTTP status {status_code}")


class ResponsePayloadError(OpenAICompatibleError):
    """The response body did not match the Chat Completions response shape."""


class UsagePayloadError(ResponsePayloadError):
    """The response usage ledger was absent or malformed."""


@dataclass(frozen=True)
class HTTPResponse:
    """Complete HTTP response returned by an :class:`HTTPTransport`."""

    status: int
    body: bytes


@runtime_checkable
class HTTPTransport(Protocol):
    """Injectable one-shot HTTP transport.

    Implementations receive the already encoded JSON body and must return the
    complete response body.  They must not retry.  A callable with this same
    keyword signature is accepted as a lightweight alternative.
    """

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> HTTPResponse:
        ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep an Authorization header from being forwarded across redirects."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


class UrllibHTTPTransport:
    """One-attempt transport implemented entirely with :mod:`urllib`."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
    ) -> HTTPResponse:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                status = response.getcode()
                response_body = response.read()
        except urllib.error.HTTPError as exc:
            # HTTPError is also a readable response.  The adapter only needs
            # the status; omitting the provider error body avoids propagating
            # account metadata in exception messages.
            status = exc.code
            response_body = b""
            exc.close()
        except ValueError:
            raise TransportError(
                category="local_request_configuration",
                delivery_ambiguous=False,
            ) from None
        except OSError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                category = "timeout"
            elif isinstance(reason, socket.gaierror):
                category = "dns"
            elif isinstance(reason, ssl.SSLError):
                category = "tls"
            elif isinstance(reason, ConnectionRefusedError):
                category = "connection_refused"
            elif isinstance(reason, (ConnectionResetError, BrokenPipeError)):
                category = "connection_reset"
            else:
                category = "network_io"
            raise TransportError(
                category=category,
                delivery_ambiguous=True,
            ) from None
        return HTTPResponse(status=status, body=response_body)

    def __repr__(self) -> str:
        return "UrllibHTTPTransport()"


class _Secret:
    """In-memory credential whose ordinary string forms are always redacted."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal_for_authorization(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted API key>"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        raise TypeError("API credentials cannot be serialized")


def normalize_chat_completions_url(base_url: str) -> str:
    """Return an absolute URL ending in exactly ``/chat/completions``.

    A version prefix such as ``/v1`` is retained.  A caller may pass either a
    provider root/version URL or the complete endpoint.  Query strings,
    fragments, and URL-embedded credentials are rejected so secrets cannot be
    smuggled into diagnostic URLs.
    """

    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty absolute HTTP(S) URL")
    parts = urllib.parse.urlsplit(base_url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError("base_url must be a non-empty absolute HTTP(S) URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("base_url must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("base_url must not contain a query string or fragment")

    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    if not path.startswith("/"):
        path = f"/{path}"
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, path, "", "")
    )


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _static_body(
    extra_body: Mapping[str, Any] | None,
    request_overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate and detach static, non-experimental provider extensions."""

    merged: dict[str, Any] = {}
    for field, source in (
        ("extra_body", extra_body),
        ("request_overrides", request_overrides),
    ):
        if source is None:
            continue
        if not isinstance(source, Mapping):
            raise TypeError(f"{field} must be a mapping")
        non_string_keys = [key for key in source if not isinstance(key, str)]
        if non_string_keys:
            raise ValueError(f"{field} keys must be strings")
        protected = sorted(_PROTECTED_REQUEST_FIELDS.intersection(source))
        if protected:
            names = ", ".join(protected)
            raise ValueError(f"{field} cannot override protected request fields: {names}")
        duplicates = sorted(set(merged).intersection(source))
        if duplicates:
            names = ", ".join(duplicates)
            raise ValueError(f"extra_body and request_overrides duplicate fields: {names}")
        merged.update(source)

    thinking = merged.get("thinking")
    if thinking is not None:
        if not isinstance(thinking, Mapping) or thinking.get("type") != "disabled":
            raise ValueError(
                "thinking must be {'type': 'disabled'} so temperature remains the treatment"
            )

    try:
        # The round trip both proves wire compatibility and prevents later
        # mutation of caller-owned nested mappings.
        return json.loads(
            json.dumps(
                merged,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("static request fields must be finite JSON values") from exc


def _usage_integer(usage: Mapping[str, Any], field: str) -> int:
    value = usage.get(field)
    if type(value) is not int or value < 0:
        raise UsagePayloadError(f"usage.{field} must be a non-negative integer")
    return value


def _optional_usage_integer(usage: Mapping[str, Any], field: str) -> int | None:
    value = usage.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise UsagePayloadError(f"usage.{field} must be a non-negative integer or null")
    return value


def _response_string(container: Mapping[str, Any], field: str, path: str) -> str:
    value = container.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ResponsePayloadError(f"{path} must be a non-empty string")
    return value


def _classify_candidate_content(content: Any) -> tuple[str, str]:
    """Return a safe expression/sentinel and a closed format classification.

    The assistant's raw content is inspected only long enough to classify the
    response. Malformed content and non-expression JSON values are discarded;
    neither this function nor its caller retains or reports them.
    """

    if content is None:
        return _INVALID_CANDIDATE_SENTINEL, "null_content"
    if not isinstance(content, str):
        return _INVALID_CANDIDATE_SENTINEL, "non_string_content"
    if not content.strip():
        return _INVALID_CANDIDATE_SENTINEL, "empty_content"
    try:
        candidate = json.loads(content)
    except json.JSONDecodeError:
        return _INVALID_CANDIDATE_SENTINEL, "invalid_json"
    if not isinstance(candidate, Mapping):
        return _INVALID_CANDIDATE_SENTINEL, "json_non_object"
    if "expression" not in candidate:
        return _INVALID_CANDIDATE_SENTINEL, "missing_expression"
    if set(candidate) != {"expression"}:
        return _INVALID_CANDIDATE_SENTINEL, "extra_fields"
    expression = candidate["expression"]
    if not isinstance(expression, str):
        return _INVALID_CANDIDATE_SENTINEL, "non_string_expression"
    if not expression.strip():
        return _INVALID_CANDIDATE_SENTINEL, "empty_expression"
    return expression, "json_expression"


def _extract_response(
    payload: Any,
) -> tuple[
    str,
    int,
    int,
    str,
    str,
    int | None,
    int | None,
    int | None,
    str,
    str | None,
]:
    if not isinstance(payload, Mapping):
        raise ResponsePayloadError("chat completions response must be a JSON object")

    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ResponsePayloadError("response.choices must contain exactly one item")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ResponsePayloadError("response.choices[0] must be an object")
    finish_reason = _response_string(
        choice,
        "finish_reason",
        "response.choices[0].finish_reason",
    )
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ResponsePayloadError("response.choices[0].message must be an object")
    content = message.get("content")
    # Candidate-format failures are experimental outcomes, not transport
    # failures. Map malformed/empty assistant content to a fixed non-DSL value
    # so the runner records ``parse_or_grammar`` and the paid slot is not
    # silently discarded, retried, or mistaken for a tuple-like AST. Outer
    # envelope/model/usage failures still fail closed because their accounting
    # cannot be audited.
    expression, candidate_format = _classify_candidate_content(content)

    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        raise UsagePayloadError("response.usage must be an object")
    input_tokens = _usage_integer(usage, "prompt_tokens")
    output_tokens = _usage_integer(usage, "completion_tokens")
    prompt_cache_hit_tokens = _optional_usage_integer(
        usage, "prompt_cache_hit_tokens"
    )
    prompt_cache_miss_tokens = _optional_usage_integer(
        usage, "prompt_cache_miss_tokens"
    )
    completion_details = usage.get("completion_tokens_details")
    if completion_details is not None and not isinstance(completion_details, Mapping):
        raise UsagePayloadError("usage.completion_tokens_details must be an object or null")
    reasoning_tokens = (
        _optional_usage_integer(completion_details, "reasoning_tokens")
        if isinstance(completion_details, Mapping)
        else None
    )
    provider_model = _response_string(payload, "model", "response.model")
    provider_fingerprint_value = payload.get("system_fingerprint")
    if provider_fingerprint_value is not None and (
        not isinstance(provider_fingerprint_value, str)
        or not provider_fingerprint_value.strip()
    ):
        raise ResponsePayloadError(
            "response.system_fingerprint must be a non-empty string or null"
        )
    provider_fingerprint = provider_fingerprint_value
    return (
        expression,
        input_tokens,
        output_tokens,
        provider_model,
        finish_reason,
        prompt_cache_hit_tokens,
        prompt_cache_miss_tokens,
        reasoning_tokens,
        candidate_format,
        provider_fingerprint,
    )


class OpenAICompatibleGenerator:
    """One-attempt candidate generator for a fixed endpoint and model."""

    __slots__ = (
        "_api_key",
        "_clock",
        "_extra_body",
        "_model",
        "_transport",
        "endpoint",
        "seed_supported",
        "timeout",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        seed_supported: bool = False,
        timeout: float = 60.0,
        extra_body: Mapping[str, Any] | None = None,
        request_overrides: Mapping[str, Any] | None = None,
        transport: HTTPTransport | Callable[..., HTTPResponse] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.endpoint = normalize_chat_completions_url(base_url)
        self._api_key = _Secret(_non_empty_string(api_key, "api_key"))
        self._model = _non_empty_string(model, "model")
        if type(seed_supported) is not bool:
            raise TypeError("seed_supported must be a boolean")
        self.seed_supported = seed_supported
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a positive finite number")
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self._extra_body = _static_body(extra_body, request_overrides)
        self._transport = transport if transport is not None else UrllibHTTPTransport()
        if not callable(getattr(self._transport, "post", None)) and not callable(
            self._transport
        ):
            raise TypeError("transport must expose post() or be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock

    @property
    def model(self) -> str:
        return self._model

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(endpoint={self.endpoint!r}, model={self._model!r}, "
            f"seed_supported={self.seed_supported!r}, timeout={self.timeout!r})"
        )

    def _post(self, *, headers: Mapping[str, str], body: bytes) -> HTTPResponse:
        post = getattr(self._transport, "post", None)
        call = post if callable(post) else self._transport
        try:
            response = call(
                url=self.endpoint,
                headers=headers,
                body=body,
                timeout=self.timeout,
            )
        except TransportError:
            raise
        except Exception:
            # Do not incorporate arbitrary transport text: an SDK or test
            # transport could include request headers in its exception.
            raise TransportError(
                category="injected_transport_exception",
                delivery_ambiguous=True,
            ) from None
        if not isinstance(response, HTTPResponse):
            raise TransportError(
                "HTTP transport must return HTTPResponse",
                category="local_transport_contract",
                delivery_ambiguous=False,
            )
        return response

    def generate(
        self,
        prompt: str,
        *,
        temperature: float,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        round_index: int = 0,
        candidate_index: int = 0,
        seed: int | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> GenerationResponse:
        """Issue exactly one request and return a validated usage envelope."""

        del round_index, candidate_index, state
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("prompt must be a non-empty string")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise TypeError("temperature must be a finite non-negative number")
        normalized_temperature = float(temperature)
        if not math.isfinite(normalized_temperature) or normalized_temperature < 0:
            raise ValueError("temperature must be a finite non-negative number")
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if seed is not None and type(seed) is not int:
            raise TypeError("seed must be an integer or None")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": normalized_temperature,
            "max_tokens": max_output_tokens,
            "response_format": {"type": "json_object"},
            **self._extra_body,
        }
        if self.seed_supported and seed is not None:
            payload["seed"] = seed
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = MappingProxyType(
            {
                "Authorization": (
                    f"Bearer {self._api_key.reveal_for_authorization()}"
                ),
                "Content-Type": "application/json",
            }
        )

        started = self._clock()
        response = self._post(headers=headers, body=body)
        elapsed_ms = max(0.0, (self._clock() - started) * 1000.0)

        if type(response.status) is not int or not 100 <= response.status <= 599:
            raise TransportError(
                "HTTP transport returned an invalid status code",
                category="local_transport_contract",
                delivery_ambiguous=False,
            )
        if not 200 <= response.status < 300:
            raise HTTPStatusError(response.status)
        if not isinstance(response.body, bytes):
            raise TransportError(
                "HTTP transport returned a non-bytes body",
                category="local_transport_contract",
                delivery_ambiguous=False,
            )
        try:
            decoded = response.body.decode("utf-8")
        except UnicodeDecodeError:
            raise ResponsePayloadError("response body must be UTF-8 JSON") from None
        try:
            response_payload = json.loads(decoded)
        except json.JSONDecodeError:
            raise ResponsePayloadError("response body must be valid JSON") from None

        (
            expression,
            input_tokens,
            output_tokens,
            provider_model,
            finish_reason,
            prompt_cache_hit_tokens,
            prompt_cache_miss_tokens,
            reasoning_tokens,
            candidate_format,
            provider_fingerprint,
        ) = _extract_response(response_payload)
        return GenerationResponse(
            expression=expression,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms,
            provider_request_count=1,
            seed_supported=self.seed_supported,
            provider_model=provider_model,
            finish_reason=finish_reason,
            prompt_cache_hit_tokens=prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=prompt_cache_miss_tokens,
            reasoning_tokens=reasoning_tokens,
            candidate_format=candidate_format,
            provider_fingerprint=provider_fingerprint,
        )


class OpenAICompatibleGeneratorFactory:
    """Create fresh generators from the experiment's sanitized context.

    ``evidence`` defaults to false so a new endpoint is not accidentally
    promoted to scientific evidence before its identity and seed behavior are
    checked.  Both that marker and the summary ``mode`` are explicit factory
    configuration.
    """

    __slots__ = (
        "_api_key",
        "_base_url",
        "_clock",
        "_extra_body",
        "_model",
        "_timeout",
        "_transport",
        "evidence",
        "evidence_reason",
        "mode",
        "seed_supported",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        seed_supported: bool = False,
        evidence: bool = False,
        mode: str = "openai-compatible-chat-completions",
        evidence_reason: str | None = None,
        timeout: float = 60.0,
        extra_body: Mapping[str, Any] | None = None,
        request_overrides: Mapping[str, Any] | None = None,
        transport: HTTPTransport | Callable[..., HTTPResponse] | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        # Validate public configuration now, before an experiment starts.  A
        # generator validates it again only through already detached values.
        self._base_url = normalize_chat_completions_url(base_url)
        self._api_key = _Secret(_non_empty_string(api_key, "api_key"))
        self._model = _non_empty_string(model, "model")
        if type(seed_supported) is not bool:
            raise TypeError("seed_supported must be a boolean")
        self.seed_supported = seed_supported
        if type(evidence) is not bool:
            raise TypeError("evidence must be a boolean")
        self.evidence = evidence
        self.mode = _non_empty_string(mode, "mode")
        if evidence_reason is not None and not isinstance(evidence_reason, str):
            raise TypeError("evidence_reason must be a string or None")
        self.evidence_reason = evidence_reason
        self._extra_body = _static_body(extra_body, request_overrides)
        self._timeout = timeout
        self._transport = transport
        self._clock = clock

        # Exercise the remaining constructor validation without performing any
        # HTTP request or retaining another credential copy.
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a positive finite number")
        if not math.isfinite(float(timeout)) or float(timeout) <= 0:
            raise ValueError("timeout must be a positive finite number")
        has_post = callable(getattr(transport, "post", None))
        if transport is not None and not has_post and not callable(transport):
            raise TypeError("transport must expose post() or be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self._base_url!r}, model={self._model!r}, "
            f"seed_supported={self.seed_supported!r}, evidence={self.evidence!r}, "
            f"mode={self.mode!r})"
        )

    def __call__(self, context: GeneratorContext) -> OpenAICompatibleGenerator:
        if not isinstance(context, GeneratorContext):
            raise TypeError("context must be a GeneratorContext")
        configured_model = context.model.get("name")
        if configured_model != self._model:
            raise ValueError(
                "factory model must exactly match GeneratorContext.model.name"
            )
        if context.model.get("structured_output") is not True:
            raise ValueError("GeneratorContext must require structured output")
        return OpenAICompatibleGenerator(
            base_url=self._base_url,
            api_key=self._api_key.reveal_for_authorization(),
            model=self._model,
            seed_supported=self.seed_supported,
            timeout=float(self._timeout),
            extra_body=self._extra_body,
            transport=self._transport,
            clock=self._clock,
        )


# Short aliases keep the integration call site readable while the longer names
# state the runner protocol role precisely.
OpenAICompatibleFactory = OpenAICompatibleGeneratorFactory
OpenAICompatibleProvider = OpenAICompatibleGenerator


__all__ = [
    "CANDIDATE_FORMATS",
    "HTTPResponse",
    "HTTPStatusError",
    "HTTPTransport",
    "OpenAICompatibleError",
    "OpenAICompatibleFactory",
    "OpenAICompatibleGenerator",
    "OpenAICompatibleGeneratorFactory",
    "OpenAICompatibleProvider",
    "ResponsePayloadError",
    "TransportError",
    "UrllibHTTPTransport",
    "UsagePayloadError",
    "normalize_chat_completions_url",
]
