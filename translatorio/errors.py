"""What the API can refuse, as types you can branch on.

The distinction this module exists for is 402 against 429. They get confused constantly and
the correct client behaviour is opposite: retry a rate limit, and stop dead on a quota. A
client that retries both hammers the second one forever and never succeeds.

Every refusal from the API has the same shape, ``{"error": {"code", "message", ...}}``, so
the code travels on the exception and a caller branches on it rather than on a sentence.
"""

from __future__ import annotations

from typing import Any


class TranslatorioError(Exception):
    """Anything the API refused, or a call that never got an answer."""

    #: Whether asking again could plausibly work.
    retryable = False

    # The status, the payload and the code travel with the error because branching on a
    # message is what a client does when the exception does not carry them.
    def __init__(
        self,
        message: str,
        status: int = 0,
        code: str | None = None,
        payload: dict[str, Any] | None = None,
        wait: float | None = None,
    ) -> None:
        """Build the error with what the response said about it.

        Args:
            message: the sentence the API sent, or ours when it sent none.
            status: the HTTP status, 0 when nothing came back at all.
            code: the API's ``error.code``, the thing to branch on.
            payload: the decoded body, as far as it could be decoded.
            wait: seconds the API asked us to wait (``error.retry_after``, or the
                ``Retry-After`` header), when it said.
        """
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.payload = payload or {}
        self.wait = wait


class AuthenticationError(TranslatorioError):
    """The key was missing, unrecognised or revoked. A wrong key stays wrong."""


class QuotaExhausted(TranslatorioError):
    """The account cannot pay for this call.

    **Never retried by anything in this library.** Catch it, stop calling and tell
    somebody: no amount of waiting produces credits.
    """

    def _credits(self) -> dict[str, Any]:
        block = self.payload.get("credits")
        return block if isinstance(block, dict) else {}

    @property
    def remaining(self) -> int:
        """Credits left on the account."""
        return int(self._credits().get("remaining") or 0)

    @property
    def required(self) -> int:
        """What this call was estimated at.

        An estimate, so a 402 can arrive with credits still in the account: a call is
        priced beforehand and refused when the estimate does not fit, and the exact charge
        is only known afterwards.
        """
        return int(self._credits().get("required") or 0)

    @property
    def queued(self) -> int:
        """Credits set aside by batches still running. Only a refused batch says it."""
        return int(self._credits().get("queued") or 0)

    @property
    def renews_at(self) -> str | None:
        """When the monthly allowance comes back, ISO 8601, or None."""
        renews = self._credits().get("renews_at")

        return str(renews) if renews is not None else None


class RateLimited(TranslatorioError):
    """Too many requests. Retried automatically; if you see it, the retries ran out."""

    retryable = True

    @property
    def retry_after(self) -> float:
        """Seconds the service asked us to wait (``error.retry_after``), or 1."""
        return max(1.0, self.wait or 1.0)


class InvalidRequest(TranslatorioError):
    """The request was malformed and nothing was translated or changed."""

    @property
    def fields(self) -> dict[str, list[str]]:
        """The fields it refused, each with its messages.

        A validation failure lists every field, and a refusal about one field (an unknown
        language, an unknown field) names that one, in the same shape. Empty when the
        refusal is about the request as a whole.
        """
        fields = _error(self.payload).get("fields")

        if not isinstance(fields, dict):
            return {}

        return {str(k): [str(m) for m in v] if isinstance(v, list) else [str(v)]
                for k, v in fields.items()}


class NotFound(TranslatorioError):
    """No such endpoint, glossary, batch, call, key or correction on this account."""


class ServerError(TranslatorioError):
    """Our fault, or the network's. Retried automatically."""

    retryable = True


def _error(payload: dict[str, Any]) -> dict[str, Any]:
    error = payload.get("error")
    return error if isinstance(error, dict) else {}


def error_for(status: int, payload: dict[str, Any], wait: float | None = None) -> TranslatorioError:
    """Return the right exception for a status.

    The wait comes from ``error.retry_after`` in the body, which every 429 and the
    in-flight 409 carry, and falls back to the ``Retry-After`` header the caller read.
    """
    error = _error(payload)
    retry_after = error.get("retry_after")

    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
        wait = max(0.0, float(retry_after))
    message = str(error.get("message") or payload.get("message") or "The request failed.")
    code = error.get("code") if isinstance(error.get("code"), str) else None

    if status == 401:
        return AuthenticationError(message, status, code, payload, wait)
    if status == 402:
        return QuotaExhausted(message, status, code, payload, wait)
    if status == 404:
        return NotFound(message, status, code, payload, wait)
    if status == 422:
        return InvalidRequest(message, status, code, payload, wait)
    if status == 429:
        return RateLimited(message, status, code, payload, wait)
    # A 409 `idempotency_in_flight` is the earlier attempt at this very call still running,
    # so waiting and asking again is exactly right. A 503 `translation_unavailable` says
    # nothing was charged and to retry. Any other 4xx is a refusal asking again cannot change.
    if (status == 409 and code == "idempotency_in_flight") or status >= 500:
        return ServerError(message, status, code, payload, wait)

    return TranslatorioError(message, status, code, payload, wait)
