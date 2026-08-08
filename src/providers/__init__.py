"""Provider adapters for candidate generation."""

from .openai_compatible import (
    HTTPResponse,
    HTTPStatusError,
    HTTPTransport,
    OpenAICompatibleError,
    OpenAICompatibleFactory,
    OpenAICompatibleGenerator,
    OpenAICompatibleGeneratorFactory,
    OpenAICompatibleProvider,
    ResponsePayloadError,
    TransportError,
    UrllibHTTPTransport,
    UsagePayloadError,
    normalize_chat_completions_url,
)

__all__ = [
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
