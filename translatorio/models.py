"""The answers, as thin objects over the JSON.

Every accessor here maps to a field the API actually returns, and ``raw`` holds the whole
answer for anything without one. An accessor for a field the API never sends would be a
promise that quietly answers ``None`` for ever.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


class _Record:
    """Anything the API answers with: the decoded JSON, kept whole."""

    def __init__(self, raw: dict[str, Any]) -> None:
        """Wrap the decoded JSON of one answer."""
        self.raw = raw

    def __repr__(self) -> str:
        """Show the class and the raw answer, which is everything there is to see."""
        return f"{type(self).__name__}({self.raw!r})"


class _Charged(_Record):
    """The part every paid call answers with: the call's name, what it used and what it cost."""

    @property
    def id(self) -> str | None:
        """The call's own name (``tr_`` and a ULID), as it appears in the history."""
        return _str(self.raw.get("id"))

    @property
    def reference(self) -> str | None:
        """Your own id for the call, handed back."""
        return _str(self.raw.get("reference"))

    @property
    def usage(self) -> dict[str, Any]:
        """Everything the call used: characters, counts, the model block and the tokens."""
        return _dict(self.raw.get("usage"))

    @property
    def characters(self) -> int:
        """Characters the call was measured on."""
        return _int(self.usage.get("characters"))

    @property
    def model(self) -> dict[str, Any]:
        """The model block as sent: ``{"read": ..., "effort": ..., "why": ...}``."""
        return _dict(self.usage.get("model"))

    @property
    def model_read(self) -> bool:
        """Whether a model read anything for this call."""
        return self.model.get("read") is True

    @property
    def model_why(self) -> str | None:
        """Why no model read it: ``test_key`` or ``memory``. None when one did."""
        return _str(self.model.get("why"))

    @property
    def tokens(self) -> dict[str, int]:
        """The model's tokens, ``{"input": n, "output": n}``."""
        tokens = _dict(self.usage.get("tokens"))
        return {"input": _int(tokens.get("input")), "output": _int(tokens.get("output"))}

    @property
    def charged(self) -> int:
        """Credits this call cost."""
        return _int(_dict(self.raw.get("credits")).get("charged"))

    @property
    def remaining(self) -> int | None:
        """Credits left on the account after it, or None when the answer did not say."""
        remaining = _dict(self.raw.get("credits")).get("remaining")
        return _int(remaining) if remaining is not None else None

    @property
    def mode(self) -> str | None:
        """``live`` or ``test``: which kind of key paid for it."""
        return _str(self.raw.get("mode"))

    @property
    def test(self) -> bool:
        """Answered for a test key: free, and never read by a model."""
        return self.mode == "test"

    @property
    def took_ms(self) -> int:
        """How long the call took on our side, in milliseconds."""
        return _int(self.raw.get("took_ms"))


class Translation(_Charged):
    """The answer to ``translate()``.

    ``results`` holds one entry per text, in the order sent, each with every language asked
    for. ``translation`` is a convenience for the commonest call, one text into one
    language, and is None for every other.
    """

    @property
    def translation(self) -> str | None:
        """The translation, when the call sent one ``text`` into one ``target``."""
        return _str(self.raw.get("translation"))

    @property
    def results(self) -> list[dict[str, Any]]:
        """One per text: ``{"index", "translations": {language: text}, "intact"}``."""
        return [row for row in _list(self.raw.get("results")) if isinstance(row, dict)]

    @property
    def translations(self) -> list[dict[str, str]]:
        """Every text's translations, ``language -> text``, in the order the texts were sent."""
        return [_dict(row.get("translations")) for row in self.results]

    def text(self, language: str, index: int = 0) -> str | None:
        """One text's translation into one language, or None when it is not in the answer.

        ``language`` is matched as the API wrote it, which is its normalised code (``es``,
        ``pt-BR``); the targets the call resolved to are in ``targets``.
        """
        for row in self.results:
            if row.get("index") == index:
                return _str(_dict(row.get("translations")).get(language))

        return None

    @property
    def source(self) -> str | None:
        """The source language: the one given, or the one detected."""
        return _str(_dict(self.raw.get("source")).get("language"))

    @property
    def detected(self) -> bool:
        """Whether the source language was detected rather than given."""
        return _dict(self.raw.get("source")).get("detected") is True

    @property
    def targets(self) -> list[str]:
        """The target languages, as the API normalised them."""
        return [t for t in _list(self.raw.get("targets")) if isinstance(t, str)]

    @property
    def intact(self) -> bool:
        """False when any placeholder, tag or masked value did not come back.

        Check those before publishing: a translation that lost ``{name}`` is not one.
        """
        return self.raw.get("intact") is True

    @property
    def segments(self) -> int:
        """Text by language pairs translated."""
        return _int(self.usage.get("segments"))

    @property
    def cached(self) -> int:
        """Segments answered from the translation memory."""
        return _int(self.usage.get("cached"))

    @property
    def corrected(self) -> int:
        """Segments answered with one of your corrections."""
        return _int(self.usage.get("corrected"))

    @property
    def anonymized(self) -> int:
        """Pieces of personal data masked before the model saw the text."""
        return _int(self.usage.get("anonymized"))

    @property
    def effort(self) -> str | None:
        """The level of effort the call was served at: ``low``, ``medium`` or ``high``."""
        return _str(self.model.get("effort"))


class FileTranslation(_Charged):
    """The answer to ``translate_file()``: one file per target language."""

    @property
    def format(self) -> str | None:
        """The format the file was read as: ``json``, ``po``, ``xliff``, ``csv``..."""
        return _str(self.raw.get("format"))

    @property
    def source(self) -> str | None:
        """The source language: the one given, or the one detected."""
        return _str(_dict(self.raw.get("source")).get("language"))

    @property
    def detected(self) -> bool:
        """Whether the source language was detected rather than given."""
        return _dict(self.raw.get("source")).get("detected") is True

    @property
    def strings(self) -> int:
        """How many strings the file held."""
        return _int(self.raw.get("strings"))

    @property
    def files(self) -> list[dict[str, Any]]:
        """Every translated file: ``{"language", "name", "content", "intact"}``."""
        return [row for row in _list(self.raw.get("files")) if isinstance(row, dict)]

    def file(self, language: str) -> dict[str, Any] | None:
        """Return the translated file for one language, or None."""
        return next((row for row in self.files if row.get("language") == language), None)

    def content(self, language: str) -> str | None:
        """Return one language's translated file as text, ready to write to disk."""
        found = self.file(language)
        return _str(found.get("content")) if found else None

    @property
    def intact(self) -> bool:
        """False when any placeholder, tag or masked value did not come back in any file."""
        return self.raw.get("intact") is True

    @property
    def segments(self) -> int:
        """String by language pairs translated."""
        return _int(self.usage.get("segments"))

    @property
    def cached(self) -> int:
        """Segments answered from the translation memory."""
        return _int(self.usage.get("cached"))

    @property
    def corrected(self) -> int:
        """Segments answered with one of your corrections."""
        return _int(self.usage.get("corrected"))

    @property
    def anonymized(self) -> int:
        """Pieces of personal data masked before the model saw the strings."""
        return _int(self.usage.get("anonymized"))

    @property
    def effort(self) -> str | None:
        """The level of effort the call was served at: ``low``, ``medium`` or ``high``."""
        return _str(self.model.get("effort"))


class ImageTranslation(_Charged):
    """The answer to ``translate_image()``: every block of text in the picture, translated."""

    @property
    def source(self) -> str | None:
        """The language of the text in the picture: the one given, or the one read."""
        return _str(_dict(self.raw.get("source")).get("language"))

    @property
    def targets(self) -> list[str]:
        """The target languages, as the API normalised them."""
        return [t for t in _list(self.raw.get("targets")) if isinstance(t, str)]

    @property
    def blocks(self) -> list[dict[str, Any]]:
        """Each block: ``{"text", "box": {"x", "y", "width", "height"}, "translations"}``.

        The box is in fractions of the image (0 to 1), top-left corner first, so it scales
        with whatever size you display the picture at.
        """
        return [row for row in _list(self.raw.get("blocks")) if isinstance(row, dict)]

    @property
    def effort(self) -> str | None:
        """The level of effort the call was served at: ``low``, ``medium`` or ``high``."""
        return _str(self.model.get("effort"))


class Detection(_Charged):
    """The answer to ``detect()``."""

    @property
    def language(self) -> dict[str, Any] | None:
        """``{"code", "name", "native", "rtl"}``, or None when it could not tell."""
        language = self.raw.get("language")
        return dict(language) if isinstance(language, dict) else None

    @property
    def code(self) -> str | None:
        """The language's code, or None when it could not tell."""
        return _str((self.language or {}).get("code"))

    @property
    def confidence(self) -> float:
        """How sure, 0 to 1."""
        confidence = self.raw.get("confidence")
        return float(confidence) if isinstance(confidence, (int, float)) else 0.0

    @property
    def by(self) -> str | None:
        """How it was decided: ``script`` (free), ``model`` or ``test_key``."""
        return _str(self.raw.get("by"))


class Batch(_Record):
    """An asynchronous batch, summarised, or read back with a page of its results."""

    @property
    def id(self) -> str | None:
        """The batch's name, ``bat_`` and a ULID."""
        return _str(self.raw.get("id"))

    @property
    def reference(self) -> str | None:
        """Your own id for the batch, handed back."""
        return _str(self.raw.get("reference"))

    @property
    def status(self) -> str | None:
        """Where it is: ``queued``, ``running``, ``completed`` or ``failed``."""
        return _str(self.raw.get("status"))

    @property
    def source(self) -> str | None:
        """The source language, or None when each text is detected."""
        return _str(self.raw.get("source"))

    @property
    def targets(self) -> list[str]:
        """The target languages."""
        return [t for t in _list(self.raw.get("targets")) if isinstance(t, str)]

    @property
    def total(self) -> int:
        """Texts in the batch."""
        return _int(self.raw.get("total"))

    @property
    def done(self) -> int:
        """Texts translated so far."""
        return _int(self.raw.get("done"))

    @property
    def failed(self) -> int:
        """Texts that failed in their place."""
        return _int(self.raw.get("failed"))

    @property
    def credits_estimated(self) -> int:
        """What was set aside when the batch was accepted."""
        return _int(_dict(self.raw.get("credits")).get("estimated"))

    @property
    def credits_charged(self) -> int:
        """What it has cost so far."""
        return _int(_dict(self.raw.get("credits")).get("charged"))

    @property
    def created_at(self) -> str | None:
        """When it was accepted, ISO 8601."""
        return _str(self.raw.get("created_at"))

    @property
    def finished_at(self) -> str | None:
        """When it finished, ISO 8601, or None while it runs."""
        return _str(self.raw.get("finished_at"))

    @property
    def expires_at(self) -> str | None:
        """When its results are deleted, ISO 8601, or None until it finishes."""
        return _str(self.raw.get("expires_at"))

    @property
    def results(self) -> list[dict[str, Any]]:
        """This page of results, by index: translations, or an error in the item's place.

        Empty on a summary: only ``batch_status()`` reads results.
        """
        return [row for row in _list(self.raw.get("results")) if isinstance(row, dict)]

    @property
    def translations(self) -> dict[int, dict[str, str]]:
        """``index -> {language: text}`` for every item of this page that was translated."""
        return {
            _int(row.get("index")): _dict(row.get("translations"))
            for row in self.results
            if "translations" in row
        }

    @property
    def failures(self) -> dict[int, dict[str, Any]]:
        """``index -> {"code", "message"}`` for every item of this page that failed."""
        return {
            _int(row.get("index")): _dict(row.get("error"))
            for row in self.results
            if "error" in row
        }

    @property
    def next(self) -> str | None:
        """Where the next page of results starts, or None on the last one.

        Opaque: pass it back as ``batch_status(..., cursor=...)``.
        """
        after = self.raw.get("next_after")

        if isinstance(after, bool) or not isinstance(after, (int, str)):
            return None

        return str(after)

    @property
    def page(self) -> Page[dict[str, Any]]:
        """This page of results as a ``Page``, the shape every paged listing answers in."""
        return Page(self.results, self.next, self.raw)


class Glossary(_Record):
    """A glossary: a list of terms with the translation they must always get."""

    @property
    def slug(self) -> str | None:
        """Its address, and what ``glossary=`` names in a call."""
        return _str(self.raw.get("slug"))

    @property
    def name(self) -> str | None:
        """Its name."""
        return _str(self.raw.get("name"))

    @property
    def source(self) -> str | None:
        """The source language it is for, or None for any."""
        return _str(self.raw.get("source"))

    @property
    def target(self) -> str | None:
        """The target language it is for, or None for every language."""
        return _str(self.raw.get("target"))

    @property
    def entries(self) -> list[dict[str, str]]:
        """Its entries, ``[{"term", "translation"}]``, in the shape an update accepts back.

        Empty in the listing, which carries only a count (``entry_count``).
        """
        return [row for row in _list(self.raw.get("entries")) if isinstance(row, dict)]

    @property
    def terms(self) -> dict[str, str]:
        """The entries as ``term -> translation``."""
        return {str(row.get("term")): str(row.get("translation")) for row in self.entries}

    @property
    def entry_count(self) -> int:
        """How many entries it has, from the listing's count or the entries themselves."""
        entries = self.raw.get("entries")
        return len(entries) if isinstance(entries, list) else _int(entries)

    @property
    def updated_at(self) -> str | None:
        """When it last changed, ISO 8601."""
        return _str(self.raw.get("updated_at"))


class Correction(_Record):
    """Your own translation of one exact text into one language."""

    @property
    def id(self) -> int:
        """Its id, which ``delete_correction()`` takes."""
        return _int(self.raw.get("id"))

    @property
    def text(self) -> str | None:
        """The exact text it answers for."""
        return _str(self.raw.get("text"))

    @property
    def target(self) -> str | None:
        """The language it is in."""
        return _str(self.raw.get("target"))

    @property
    def translation(self) -> str | None:
        """Your translation."""
        return _str(self.raw.get("translation"))

    @property
    def updated_at(self) -> str | None:
        """When it was last saved, ISO 8601."""
        return _str(self.raw.get("updated_at"))


class TranslationRecord(_Record):
    """One call in the history: what was asked and what it cost. Never the text."""

    @property
    def id(self) -> str | None:
        """The call's name, ``tr_`` and a ULID."""
        return _str(self.raw.get("id"))

    @property
    def reference(self) -> str | None:
        """Your own id for the call."""
        return _str(self.raw.get("reference"))

    @property
    def kind(self) -> str | None:
        """``text``, ``image`` or ``detect``."""
        return _str(self.raw.get("kind"))

    @property
    def channel(self) -> str | None:
        """Where it came from: ``api`` or the panel."""
        return _str(self.raw.get("channel"))

    @property
    def source(self) -> str | None:
        """The source language."""
        return _str(self.raw.get("source"))

    @property
    def targets(self) -> list[str]:
        """The target languages."""
        return [t for t in _list(self.raw.get("targets")) if isinstance(t, str)]

    @property
    def format(self) -> str | None:
        """The format the text was read as."""
        return _str(self.raw.get("format"))

    @property
    def characters(self) -> int:
        """Characters measured."""
        return _int(self.raw.get("characters"))

    @property
    def segments(self) -> int:
        """Text by language pairs."""
        return _int(self.raw.get("segments"))

    @property
    def cached(self) -> int:
        """Segments answered from memory."""
        return _int(self.raw.get("cached"))

    @property
    def anonymized(self) -> int:
        """Pieces of personal data masked."""
        return _int(self.raw.get("anonymized"))

    @property
    def credits(self) -> int:
        """Credits it cost."""
        return _int(self.raw.get("credits"))

    @property
    def took_ms(self) -> int:
        """How long it took, in milliseconds."""
        return _int(self.raw.get("took_ms"))

    @property
    def created_at(self) -> str | None:
        """When it was made, ISO 8601."""
        return _str(self.raw.get("created_at"))


T = TypeVar("T")


@dataclass
class Page(Generic[T]):
    """One page of a paged listing.

    ``next`` is opaque: pass it back as the method's ``cursor`` argument for the following
    page, whatever the API calls it underneath. None means this is the last page. Paged by
    a position and never by an offset, because rows keep arriving at the top and an offset
    would skip whatever was inserted above it.
    """

    items: list[T]
    next: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __iter__(self) -> Iterator[T]:
        """Iterate over the page's rows."""
        return iter(self.items)

    def __len__(self) -> int:
        """How many rows the page holds."""
        return len(self.items)
