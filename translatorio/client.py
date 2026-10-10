"""The Translatorio API, from Python."""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable, Protocol, TypeVar, Union

from .errors import ServerError, TranslatorioError, error_for
from .models import (
    Batch,
    Correction,
    Detection,
    FileTranslation,
    Glossary,
    ImageTranslation,
    Page,
    Translation,
    TranslationRecord,
)

#: The one place the version is written, besides ``pyproject.toml``: the publish workflow
#: writes the tag into both, and ``test_the_constant_and_the_manifest_agree`` checks they
#: agree, so the User-Agent cannot claim a release it is not.
VERSION = "1.0.0"

#: What a transport answers: the status and the body, and optionally the headers, which is
#: how a ``Retry-After`` reaches the retry loop.
R = TypeVar("R")

Response = Union[tuple[int, str], tuple[int, str, Mapping[str, str]]]


class Transport(Protocol):
    """How a request actually leaves the machine.

    One method, so the client depends on no HTTP library at all, with nothing to conflict
    with whatever your application already pins, and so tests can drive it against a stub.

    Return ``(status, body)`` or ``(status, body, headers)``. Report a request that never
    got an answer (refused, timed out, cut off) by raising ``ServerError``: that is what the
    client retries.
    """

    def send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Response:  # pragma: no cover - interface
        """Send the request and return ``(status, body[, headers])``."""
        ...


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Hand a 3xx back instead of following it.

    urllib follows a POST answered with 302 as a GET to wherever ``Location`` says,
    carrying the ``Authorization`` header with it. The API never redirects, so a redirect
    is a proxy, a captive portal or a misconfigured base URL: following it sends the key
    somewhere it was not meant for and reads that page as the answer.
    """

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Refuse every redirect, which makes urllib raise it as an ``HTTPError``."""
        return None


class UrllibTransport:
    """The standard library, and nothing else.

    A client for one small API is not worth a dependency tree, and a library that forces a
    major version of ``requests`` or ``httpx`` on its host is a library that gets removed
    the first time the host disagrees.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        """Build a transport whose socket operations give up after ``timeout`` seconds.

        Connecting and every read each have the full ``timeout``, which is what urllib's
        timeout means: a stalled body fails like a stalled connection.
        """
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirects)

    def send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> Response:
        """Send the request and return ``(status, body, headers)``.

        Every failure of the connection itself becomes a ``ServerError``, which the client
        retries. That includes the ones urllib does not wrap: a read timeout arrives as a
        bare ``TimeoutError``, a truncated or garbled response as ``http.client``'s own
        ``IncompleteRead`` or ``BadStatusLine``, and reading the body of an error response
        can time out as well. Any of those escaping untyped would pass every
        ``except TranslatorioError`` the caller wrote, on the failure most worth retrying.
        """
        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    return (
                        response.status,
                        response.read().decode("utf-8", "replace"),
                        dict(response.headers.items()),
                    )
            except urllib.error.HTTPError as e:
                # Read here, inside the outer try: the body of an error can stall too.
                return (
                    e.code,
                    e.read().decode("utf-8", "replace"),
                    dict(e.headers.items()) if e.headers else {},
                )
        except urllib.error.URLError as e:
            # Reported as a server error because the caller does the same thing about
            # either: the request did not arrive, so ask again.
            raise ServerError(f"Could not reach Translatorio: {e.reason}") from e
        except (http.client.HTTPException, OSError) as e:
            raise ServerError(f"Could not reach Translatorio: {e!r}") from e


def _encode_image(data: bytes | str) -> str:
    """Bytes as the API wants them: standard base64, or a ``data:`` URI left alone.

    Encoding something already encoded would send the alphabet of the alphabet, and the
    service would decode one layer and find text where a picture should be. Base64 is
    therefore recognised as the rest of the world writes it, not only as ``b64encode``
    does: wrapped at 76 columns, in the URL-safe alphabet, or without its padding. What is
    sent is the cleaned, standard form, because the service decodes strictly and does not
    accept ``-`` or ``_``.
    """
    if isinstance(data, str):
        if data.startswith("data:"):
            return data

        cleaned = "".join(data.split()).replace("-", "+").replace("_", "/")

        if cleaned and len(cleaned) % 4:
            cleaned += "=" * (-len(cleaned) % 4)

        try:
            base64.b64decode(cleaned, validate=True)
        except (ValueError, binascii.Error):
            return base64.b64encode(data.encode()).decode()

        return cleaned

    return base64.b64encode(data).decode()


def _retry_after(headers: Mapping[str, str]) -> float | None:
    """Return the ``Retry-After`` header in seconds, or None when absent or a date."""
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return None

    return None


def _languages(targets: str | list[str]) -> dict[str, Any]:
    """``target`` for one language, ``targets`` for several, as the API names them."""
    if isinstance(targets, str):
        return {"target": targets}

    return {"targets": list(targets)}


def _given(**fields: Any) -> dict[str, Any]:
    """Only the options the caller actually set.

    The API refuses a field it does not know and treats a sent ``null`` as a value, so an
    option left out has to be left out of the body too.
    """
    return {name: value for name, value in fields.items() if value is not None}


def _form_fields(fields: Mapping[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Flatten options into form fields the way PHP reads a form back into arrays.

    A list becomes ``targets[]`` once per item, a dict ``terms[es][cart]``, and a boolean
    ``1`` or ``0``, which Laravel's ``boolean`` rule and ``$request->boolean()`` both accept
    (``true`` and ``false`` as words are refused by the first).
    """
    flat: list[tuple[str, str]] = []

    for key, value in fields.items():
        name = f"{prefix}[{key}]" if prefix else str(key)

        if isinstance(value, Mapping):
            flat.extend(_form_fields(value, name))
        elif isinstance(value, (list, tuple)):
            flat.extend((f"{name}[]", _form_value(item)) for item in value)
        else:
            flat.append((name, _form_value(value)))

    return flat


def _form_value(value: Any) -> str:
    """One form value as text: booleans as ``1``/``0``, everything else as written."""
    if isinstance(value, bool):
        return "1" if value else "0"

    return str(value)


def _quoted(value: str) -> str:
    """Make a name or filename safe inside the quotes of a ``Content-Disposition``."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")


def _multipart(
    fields: list[tuple[str, str]], file: tuple[str, str, bytes]
) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body and its ``Content-Type``.

    Built here rather than in the transport, so a transport stays one method that moves
    bytes and knows nothing about forms. The boundary is random, and drawn again in the
    vanishingly rare case the file happens to contain it.
    """
    part, filename, content = file

    while True:
        boundary = f"translatorio-{secrets.token_hex(16)}"

        if boundary.encode() not in content:
            break

    chunks = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{_quoted(name)}"\r\n\r\n'.encode()
        + value.encode()
        + b"\r\n"
        for name, value in fields
    ]

    chunks.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{_quoted(part)}"; '
        f'filename="{_quoted(filename)}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        + content
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())

    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class _Unset:
    """Marks an argument the caller did not pass, where ``None`` is a value worth sending."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


class Client:
    """The Translatorio API, from Python.

    Retries a 429, a 5xx and an unreachable service with a growing wait, and a 402 never.
    Every call that changes something carries an ``Idempotency-Key``, so a retried timeout
    is translated once and billed once.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://translatorio.com/api",
        retries: int = 2,
        timeout: float = 10.0,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_wait: float = 30.0,
    ) -> None:
        """Build a client.

        Args:
            api_key: ``tr_live_...`` or ``tr_test_...``.
            base_url: the API's root, ``/v1`` excluded, for a staging or self-hosted one.
            retries: how many times to ask again when it is worth asking again. A 429, a
                5xx or an unreachable service is retried with a growing wait; a 402 never.
            timeout: seconds for each socket operation (connecting, then each read).
                Ignored when ``transport`` is given, since a transport owns its own.
            transport: anything with a ``send()``, to put the request through your own
                HTTP stack.
            sleep: how to wait between attempts. Replaced in tests so they do not.
            max_wait: ceiling on that wait. ``Retry-After`` says how long to wait and
                honouring it is the point; honouring it without a ceiling lets a number on
                the wire decide how long your own call hangs.
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.transport = transport or UrllibTransport(timeout)
        self._sleep = sleep
        self.max_wait = max_wait

    # -- translation --------------------------------------------------------------

    def translate(
        self,
        text: str | list[str],
        targets: str | list[str],
        *,
        source: str | None = None,
        format: str | None = None,
        formality: str | None = None,
        context: str | None = None,
        glossary: str | None = None,
        terms: dict[str, Any] | None = None,
        anonymize: bool | None = None,
        reference: str | None = None,
        effort: str | None = None,
        idempotency_key: str | None = None,
    ) -> Translation:
        """Translate one text or many into one language or many.

        Args:
            text: one string, or a list of them (answered in the same order).
            targets: one language code, or a list. Codes are BCP 47: ``es``, ``pt-BR``.
            source: the source language; left out (or ``"auto"``) it is detected.
            format: ``text``, ``html`` or ``markdown``: decides what is protected.
            formality: ``default``, ``formal`` or ``informal``.
            context: what the text is and who reads it, up to 1,000 characters.
            glossary: the slug of one of your glossaries.
            terms: glossary terms for this call only, ``{"Acme": "Acme"}`` for every
                language or ``{"es": {"cart": "cesta"}}`` per language.
            anonymize: mask personal data before the model sees it. The API's default is on.
            reference: your own id for the call, handed back and kept in the history.
            effort: ``low``, ``medium`` or ``high``: how hard the model works.
            idempotency_key: your own key, to make a retry from your side the same call.
        """
        body = {"texts": list(text)} if isinstance(text, list) else {"text": text}

        return Translation(self._write("POST", "/v1/translate", {
            **body,
            **_languages(targets),
            **_given(
                source=source, format=format, formality=formality, context=context,
                glossary=glossary, terms=terms, anonymize=anonymize, reference=reference,
                effort=effort,
            ),
        }, idempotency_key))

    def translate_file(
        self,
        file: str | bytes | os.PathLike[str],
        targets: str | list[str],
        *,
        filename: str | None = None,
        format: str | None = None,
        source: str | None = None,
        formality: str | None = None,
        context: str | None = None,
        glossary: str | None = None,
        terms: dict[str, Any] | None = None,
        anonymize: bool | None = None,
        reference: str | None = None,
        effort: str | None = None,
        idempotency_key: str | None = None,
    ) -> FileTranslation:
        """Translate a whole translation file: one file back per language, same shape.

        Args:
            file: a path (``pathlib.Path``) or the file's bytes, uploaded as they are; or
                the file's content as text. A plain ``str`` is always content, never a
                path, so a file name cannot be sent off to be translated by mistake.
            targets: one language code, or a list.
            filename: the name, which tells the format and names the results. Taken from
                the path when ``file`` is one.
            format: ``json``, ``po``, ``xliff``, ``csv``, ``android``, ``ios``, ``markdown``,
                ``html`` or ``text``. Optional when the file name's extension says it.

        The other options are those of ``translate()``.

        Bytes and paths go up as ``multipart/form-data``, untouched, so a UTF-16 ``.strings``
        file arrives byte for byte; decoding it here to send it as JSON text would corrupt
        it. Text goes up as JSON ``content``.
        """
        options = _given(
            format=format, source=source, formality=formality, context=context,
            glossary=glossary, terms=terms, anonymize=anonymize, reference=reference,
            effort=effort,
        )

        if isinstance(file, str):
            return FileTranslation(self._write("POST", "/v1/translate/file", {
                "content": file,
                **_languages(targets),
                **_given(filename=filename),
                **options,
            }, idempotency_key))

        if isinstance(file, os.PathLike):
            path = os.fspath(file)

            with open(path, "rb") as handle:
                raw = handle.read()

            filename = filename or os.path.basename(path)
        else:
            raw = bytes(file)

        # The upload's own name is what the API reads, so no separate `filename` field.
        body, content_type = _multipart(
            _form_fields({**_languages(targets), **options}),
            ("file", filename or "file", raw),
        )

        return FileTranslation(self._write_raw(
            "POST", "/v1/translate/file", body, content_type, idempotency_key,
        ))

    def translate_image(
        self,
        image: str | bytes | os.PathLike[str],
        targets: str | list[str],
        *,
        source: str | None = None,
        formality: str | None = None,
        context: str | None = None,
        glossary: str | None = None,
        terms: dict[str, Any] | None = None,
        reference: str | None = None,
        effort: str | None = None,
        idempotency_key: str | None = None,
    ) -> ImageTranslation:
        """Read the text in a picture and translate every block of it.

        Args:
            image: an http or https address (fetched by the model provider); the file's
                bytes; a path (``pathlib.Path``); or a base64 string or ``data:`` URI. Not a
                guess: ``url`` accepts those two schemes and nothing else, so any other
                string is the picture itself. PNG, JPEG, WebP or GIF.
            targets: one language code, or a list.

        The other options are those of ``translate()``; an image takes no ``format`` and no
        ``anonymize``.
        """
        if isinstance(image, os.PathLike):
            with open(os.fspath(image), "rb") as handle:
                image = handle.read()

        if isinstance(image, str) and image.lower().startswith(("http://", "https://")):
            picture = {"url": image}
        else:
            picture = {"data": _encode_image(image)}

        return ImageTranslation(self._write("POST", "/v1/translate/image", {
            **picture,
            **_languages(targets),
            **_given(
                source=source, formality=formality, context=context, glossary=glossary,
                terms=terms, reference=reference, effort=effort,
            ),
        }, idempotency_key))

    def detect(
        self,
        text: str,
        *,
        reference: str | None = None,
        idempotency_key: str | None = None,
    ) -> Detection:
        """Tell which language a text is in.

        Rarely needed: leaving ``source`` out of ``translate()`` detects it in the same call.
        """
        return Detection(self._write(
            "POST", "/v1/detect", {"text": text, **_given(reference=reference)}, idempotency_key,
        ))

    # -- batches ------------------------------------------------------------------

    def batch(
        self,
        texts: list[str],
        targets: str | list[str],
        *,
        source: str | None = None,
        format: str | None = None,
        formality: str | None = None,
        context: str | None = None,
        glossary: str | None = None,
        terms: dict[str, Any] | None = None,
        anonymize: bool | None = None,
        reference: str | None = None,
        effort: str | None = None,
        idempotency_key: str | None = None,
    ) -> Batch:
        """Queue many texts, translated in the background. Answers at once with the batch.

        Poll it with ``batch_status()``, or let the ``batch.completed`` webhook say when it
        is done. The options are those of ``translate()``.
        """
        return Batch(self._write("POST", "/v1/batches", {
            "texts": list(texts),
            **_languages(targets),
            **_given(
                source=source, format=format, formality=formality, context=context,
                glossary=glossary, terms=terms, anonymize=anonymize, reference=reference,
                effort=effort,
            ),
        }, idempotency_key))

    def batches(self, *, limit: int | None = None) -> list[Batch]:
        """List the most recent batches, newest first, each summarised without its results."""
        body = self._get("/v1/batches", {"limit": limit})

        return [Batch(row) for row in body.get("data", []) if isinstance(row, dict)]

    def batch_status(
        self,
        batch_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Batch:
        """Read a batch back, with a page of its results (up to 500, 100 by default).

        The batch's ``page`` is that page as a ``Page``; pass its ``next`` back as
        ``cursor`` for the following one. None means the last page.
        """
        query = {"after": cursor, "limit": limit}

        return Batch(self._get(f"/v1/batches/{_segment(batch_id)}", query))

    # -- glossaries ---------------------------------------------------------------

    def glossaries(self) -> list[Glossary]:
        """Every glossary on the account, with entry counts rather than entries."""
        body = self._get("/v1/glossaries")

        return [Glossary(row) for row in body.get("data", []) if isinstance(row, dict)]

    def create_glossary(
        self,
        name: str,
        entries: dict[str, str] | list[dict[str, str]] | None = None,
        *,
        source: str | None = None,
        target: str | None = None,
    ) -> Glossary:
        """Create a glossary.

        Args:
            name: its name; the slug is made from it.
            entries: ``{"cart": "cesta"}``, or the ``[{"term", "translation"}]`` list a read
                returns. Up to 5,000, 200 characters per term.
            source: tie it to texts in one language. Left out, it applies to any.
            target: tie it to one target language. Left out, it applies to every language,
                which is what brand names want.
        """
        return Glossary(self._write("POST", "/v1/glossaries", {
            "name": name,
            **_given(source=source, target=target, entries=entries),
        }))

    def glossary(self, slug: str) -> Glossary:
        """One glossary, with its entries."""
        return Glossary(self._get(f"/v1/glossaries/{_segment(slug)}"))

    def update_glossary(
        self,
        slug: str,
        *,
        name: Any = UNSET,
        source: Any = UNSET,
        target: Any = UNSET,
        entries: Any = UNSET,
    ) -> Glossary:
        """Change any field of a glossary. Only what you pass is sent.

        ``entries``, when passed, REPLACES the whole list. ``source`` or ``target`` passed
        as None unties the glossary from that language.
        """
        body = {
            field: value
            for field, value in (("name", name), ("source", source), ("target", target),
                                 ("entries", entries))
            if value is not UNSET
        }

        return Glossary(self._write("PUT", f"/v1/glossaries/{_segment(slug)}", body))

    def delete_glossary(self, slug: str) -> dict[str, Any]:
        """Delete a glossary and its entries. Answers ``{"deleted": True}``."""
        return self._write("DELETE", f"/v1/glossaries/{_segment(slug)}", None)

    # -- corrections --------------------------------------------------------------

    def corrections(
        self,
        *,
        target: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[Correction]:
        """Your corrections, newest first, up to 200 a page (50 by default).

        Pass the page's ``next`` back as ``cursor`` for the next one.
        """
        body = self._get("/v1/corrections", {"target": target, "limit": limit, "cursor": cursor})

        return _page(body, Correction)

    def correct(self, text: str, target: str, translation: str) -> Correction:
        """Save your own translation of one exact text into one language.

        From then on that text, into that language, is answered with yours. Saving the same
        text and language again replaces it.
        """
        return Correction(self._write("PUT", "/v1/corrections", {
            "text": text,
            "target": target,
            "translation": translation,
        }))

    def delete_correction(self, correction_id: int | str) -> dict[str, Any]:
        """Delete a correction. Answers ``{"deleted": True}``."""
        return self._write("DELETE", f"/v1/corrections/{_segment(str(correction_id))}", None)

    # -- the account --------------------------------------------------------------

    def languages(self) -> list[dict[str, Any]]:
        """Every language: ``{"code", "name", "native", "rtl"}``. Free."""
        body = self._get("/v1/languages")

        return [row for row in body.get("data", []) if isinstance(row, dict)]

    def usage(self) -> dict[str, Any]:
        """Plan, credits, the last 30 days and prices. Free, and it answers at zero credits."""
        return self._get("/v1/usage")

    def translations(
        self,
        *,
        kind: str | None = None,
        reference: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> Page[TranslationRecord]:
        """List the account's calls, newest first, up to 100 a page (25 by default). No text.

        Args:
            kind: ``text``, ``image`` or ``detect``.
            reference: only the calls with this reference.
            limit: rows per page.
            cursor: the ``next`` of the previous page.
        """
        body = self._get("/v1/translations", {
            "kind": kind, "reference": reference, "limit": limit, "cursor": cursor,
        })

        return _page(body, TranslationRecord)

    def translation_record(self, translation_id: str) -> TranslationRecord:
        """One call from the history."""
        return TranslationRecord(self._get(f"/v1/translations/{_segment(translation_id)}"))

    def keys(self) -> list[dict[str, Any]]:
        """List the account's active keys: name, mode, prefix, last use. Never a secret."""
        body = self._get("/v1/keys")

        return [row for row in body.get("data", []) if isinstance(row, dict)]

    def revoke_key(self, key_id: int | str) -> dict[str, Any]:
        """Revoke a key, including the one you are calling with. There is no create."""
        return self._write("DELETE", f"/v1/keys/{_segment(str(key_id))}", None)

    def ping(self) -> dict[str, Any]:
        """Is the key good, is the service up: ``{"ok", "mode", "key"}``. Free."""
        return self._get("/v1/ping")

    # -- plumbing -----------------------------------------------------------------

    def _write(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a JSON write (or a bodiless DELETE) under one idempotency key."""
        body = json.dumps(payload).encode() if payload is not None else None
        content_type = "application/json" if payload is not None else None

        return self._write_raw(method, path, body, content_type, idempotency_key)

    def _write_raw(
        self,
        method: str,
        path: str,
        body: bytes | None,
        content_type: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a write whose body is already encoded, under one idempotency key."""
        # One key per call, reused by every retry of that call. That is what makes the
        # retrying safe: the same key means the same request, and the API answers it once
        # however many times the network makes us ask.
        key = idempotency_key or f"py-{secrets.token_hex(16)}"

        return self._send(method, path, {}, body, content_type, key)

    def _get(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._send("GET", path, query or {}, None, None, None)

    def _send(
        self,
        method: str,
        path: str,
        query: dict[str, Any],
        body: bytes | None,
        content_type: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        query = {k: v for k, v in query.items() if v is not None}

        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": f"translatorio-python/{VERSION}",
        }

        if content_type:
            headers["Content-Type"] = content_type

        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        attempt = 0

        while True:
            try:
                answer = self.transport.send(method, url, headers, body)
                status, raw = answer[0], answer[1]
                wait = _retry_after(answer[2]) if len(answer) > 2 else None

                if 200 <= status < 300:
                    return self._answer(status, raw)

                if status < 400:
                    # The API never redirects. A 3xx is a proxy, a portal or a wrong base
                    # URL, and whatever page it points at is not a translation.
                    raise ServerError(
                        f"Translatorio answered {status}, a redirect, which the API never "
                        "sends. Check base_url and any proxy in between.",
                        status,
                    )

                raise error_for(status, self._decode(raw), wait)
            except TranslatorioError as e:
                if not e.retryable or attempt >= self.retries:
                    raise

                attempt += 1

                # Growing, because a service that just said "too many" is not helped by
                # being asked again immediately. A Retry-After beats guessing: the service
                # knows when its own window turns over. Bounded all the same.
                self._sleep(min(
                    self.max_wait,
                    max(1.0, e.wait) if e.wait is not None else float(2**attempt),
                ))

    @staticmethod
    def _answer(status: int, raw: str) -> dict[str, Any]:
        """Return the body of a success, which has to be a JSON object or is no answer at all.

        An empty or non-JSON body read as an empty answer would hand the caller a
        translation with nothing in it, and a proxy's HTML page would pass for one. Raised
        as a retryable ``ServerError``, because a garbled answer is the network's fault.
        """
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = None

        if not isinstance(decoded, dict):
            raise ServerError(
                f"Translatorio answered {status} with a body that is not a JSON object, "
                "so there is no answer in it.",
                status,
            )

        return decoded

    @staticmethod
    def _decode(raw: str) -> dict[str, Any]:
        """Return the body of a refusal, as far as it can be read.

        Lenient on purpose, unlike ``_answer``: a 502 from a load balancer is an HTML page,
        and it must still become a ``ServerError`` rather than a decoding failure.
        """
        try:
            decoded = json.loads(raw or "{}")
        except ValueError:
            return {}

        return decoded if isinstance(decoded, dict) else {}


def _page(body: dict[str, Any], item: Callable[[dict[str, Any]], R]) -> Page[R]:
    """Read a listing paged by ``cursor``/``next_cursor`` into a ``Page``."""
    following = body.get("next_cursor")

    return Page(
        [item(row) for row in body.get("data", []) if isinstance(row, dict)],
        following if isinstance(following, str) else None,
        body,
    )


def _segment(value: str) -> str:
    """One path segment, escaped, so an id cannot walk to another endpoint."""
    return urllib.parse.quote(value, safe="")
