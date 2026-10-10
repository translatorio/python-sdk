"""The Python client, against a transport that answers like the API does.

The behaviours worth testing here are the ones a caller cannot see and would otherwise
find out from an invoice: what gets retried, what never does, and whether a retry is the
same request or a second one. The answers are shaped after the API's own controllers.
"""

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translatorio import (
    UNSET,
    AuthenticationError,
    Client,
    InvalidRequest,
    NotFound,
    Page,
    QuotaExhausted,
    RateLimited,
    ServerError,
    TranslatorioError,
)


class FakeTransport:
    """Answers with whatever it was handed, and remembers what it was asked."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, method, url, headers, body):
        content_type = headers.get("Content-Type", "")
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "raw": body,
                "body": json.loads(body) if body and content_type == "application/json" else None,
                "form": parse_multipart(body, content_type) if content_type.startswith("multipart/") else None,
                "key": headers.get("Idempotency-Key"),
            }
        )

        response = self.responses.pop(0)
        status, payload = response[0], response[1]

        if isinstance(payload, Exception):
            raise payload

        text = payload if isinstance(payload, str) else json.dumps(payload)

        return (status, text, response[2]) if len(response) > 2 else (status, text)


def parse_multipart(body, content_type):
    """Split a multipart body into ``[(name, filename, bytes)]``, byte for byte.

    Written out rather than borrowed from ``email``, which would normalise line endings
    and so hide exactly the corruption the file tests are there to catch.
    """
    boundary = content_type.split("boundary=", 1)[1].encode()
    assert body.endswith(b"--" + boundary + b"--\r\n"), "the body must close its boundary"

    parts = []

    for chunk in body.split(b"--" + boundary)[1:-1]:
        assert chunk.startswith(b"\r\n") and chunk.endswith(b"\r\n")
        head, content = chunk[2:-2].split(b"\r\n\r\n", 1)
        disposition = head.decode().split("\r\n")[0]
        name = disposition.split('name="', 1)[1].split('"', 1)[0]
        filename = disposition.split('filename="', 1)[1].split('"', 1)[0] if "filename=" in disposition else None
        parts.append((name, filename, content))

    return parts


def client(responses, **kwargs):
    transport = FakeTransport(responses)
    return Client(
        "tr_test_key",
        "https://example.test/api",
        transport=transport,
        sleep=lambda s: None,
        **kwargs,
    ), transport


USAGE = {
    "usage": {
        "characters": 11,
        "segments": 1,
        "cached": 0,
        "corrected": 0,
        "anonymized": 2,
        "model": {"read": True, "name": "gpt-5.6-luna", "why": None, "effort": "medium"},
        "tokens": {"input": 420, "output": 30},
    },
    "credits": {"charged": 3, "remaining": 1997},
    "mode": "live",
    "took_ms": 840,
}

TRANSLATION = {
    "id": "tr_01k6",
    "reference": "release-42",
    "source": {"language": "en", "detected": True},
    "targets": ["es"],
    "translation": "Añadir a la cesta",
    "results": [{"index": 0, "translations": {"es": "Añadir a la cesta"}, "intact": True}],
    "intact": True,
    **USAGE,
}

BATCH = {
    "id": "bat_01k6",
    "reference": None,
    "status": "queued",
    "source": None,
    "targets": ["es", "fr"],
    "total": 3,
    "done": 0,
    "failed": 0,
    "credits": {"estimated": 40, "charged": 0},
    "created_at": "2026-10-07T10:00:00+00:00",
    "finished_at": None,
    "expires_at": None,
}


class TranslateTest(unittest.TestCase):
    def test_one_text_into_one_language(self):
        tr, transport = client([(200, TRANSLATION)])

        result = tr.translate("Add to cart", "es", glossary="shop", reference="release-42")

        self.assertEqual("Añadir a la cesta", result.translation)
        self.assertEqual("Añadir a la cesta", result.text("es"))
        self.assertIsNone(result.text("de"))
        self.assertTrue(result.intact)
        self.assertEqual("en", result.source)
        self.assertTrue(result.detected)
        self.assertEqual(["es"], result.targets)
        self.assertEqual("tr_01k6", result.id)
        self.assertEqual("release-42", result.reference)
        self.assertEqual(3, result.charged)
        self.assertEqual(1997, result.remaining)
        self.assertEqual(2, result.anonymized)
        self.assertEqual(11, result.characters)
        self.assertTrue(result.model_read)
        self.assertEqual("gpt-5.6-luna", result.model_name)
        self.assertEqual("medium", result.effort)
        self.assertEqual({"input": 420, "output": 30}, result.tokens)
        self.assertFalse(result.test)

        sent = transport.calls[0]
        self.assertEqual("POST", sent["method"])
        self.assertEqual("https://example.test/api/v1/translate", sent["url"])
        self.assertEqual(
            {"text": "Add to cart", "target": "es", "glossary": "shop", "reference": "release-42"},
            sent["body"],
        )

    def test_many_texts_into_many_languages(self):
        answer = {
            **TRANSLATION,
            "translation": None,
            "targets": ["es", "de"],
            "results": [
                {"index": 0, "translations": {"es": "Hola", "de": "Hallo"}, "intact": True},
                {"index": 1, "translations": {"es": "{n} días", "de": "{n} Tage"}, "intact": False},
            ],
            "intact": False,
        }
        del answer["translation"]
        tr, transport = client([(200, answer)])

        result = tr.translate(
            ["Hello", "{n} days"], ["es", "de"],
            source="en", format="html", formality="informal", context="shop buttons",
            terms={"es": {"cart": "cesta"}}, anonymize=False,
        )

        self.assertIsNone(result.translation)
        self.assertEqual("Tage", result.text("de", index=1).split()[-1])
        self.assertEqual([{"es": "Hola", "de": "Hallo"}, {"es": "{n} días", "de": "{n} Tage"}], result.translations)
        self.assertFalse(result.intact)
        self.assertEqual(
            {
                "texts": ["Hello", "{n} days"],
                "targets": ["es", "de"],
                "source": "en",
                "format": "html",
                "formality": "informal",
                "context": "shop buttons",
                "terms": {"es": {"cart": "cesta"}},
                "anonymize": False,
            },
            transport.calls[0]["body"],
        )

    def test_options_left_out_are_left_out_of_the_body(self):
        """The API refuses a field it does not know and reads a sent null as a value."""
        tr, transport = client([(200, TRANSLATION)])

        tr.translate("Hi", "es")

        self.assertEqual({"text": "Hi", "target": "es"}, transport.calls[0]["body"])

    def test_effort_is_passed_through_only_when_given(self):
        tr, transport = client([(200, TRANSLATION), (200, TRANSLATION)])

        tr.translate("Hi", "es", effort="high")
        tr.translate("Hi", "es")

        self.assertEqual("high", transport.calls[0]["body"]["effort"])
        self.assertNotIn("effort", transport.calls[1]["body"])

    def test_an_unknown_option_never_reaches_the_api(self):
        tr, transport = client([])

        with self.assertRaises(TypeError):
            tr.translate("Hi", "es", locale="es")

        self.assertEqual([], transport.calls)

    def test_a_test_key_answer_says_so(self):
        answer = {**TRANSLATION, "mode": "test", "usage": {**USAGE["usage"], "model": {"read": False, "name": None, "why": "test_key"}}}
        tr, _ = client([(200, answer)])

        result = tr.translate("Hi", "es")

        self.assertTrue(result.test)
        self.assertFalse(result.model_read)
        self.assertEqual("test_key", result.model_why)


class FileTest(unittest.TestCase):
    ANSWER = {
        "id": "tr_01k7",
        "reference": None,
        "format": "json",
        "source": {"language": "en", "detected": False},
        "strings": 2,
        "files": [
            {"language": "es", "name": "es.json", "content": '{"home": "Inicio"}', "intact": True},
            {"language": "de", "name": "de.json", "content": '{"home": "Startseite"}', "intact": True},
        ],
        "intact": True,
        "usage": {"characters": 8, "segments": 4, "cached": 1, "anonymized": 0, "model": {"read": True, "name": "gpt-5.6-luna", "why": None}, "tokens": {"input": 1, "output": 1}},
        "credits": {"charged": 5, "remaining": 100},
        "mode": "live",
        "took_ms": 900,
    }

    def test_content_goes_up_as_text_with_its_name(self):
        tr, transport = client([(200, self.ANSWER)])

        result = tr.translate_file('{"home": "Home"}', ["es", "de"], filename="en.json", source="en")

        self.assertEqual("json", result.format)
        self.assertEqual(2, result.strings)
        self.assertEqual('{"home": "Startseite"}', result.content("de"))
        self.assertEqual("es.json", result.file("es")["name"])
        self.assertIsNone(result.file("fr"))
        self.assertEqual(1, result.cached)
        self.assertEqual(
            {"content": '{"home": "Home"}', "targets": ["es", "de"], "filename": "en.json", "source": "en"},
            transport.calls[0]["body"],
        )
        self.assertEqual("https://example.test/api/v1/translate/file", transport.calls[0]["url"])

    def test_the_answer_says_corrections_and_effort(self):
        answer = {**self.ANSWER, "usage": {**self.ANSWER["usage"], "corrected": 2, "model": {"read": True, "name": "gpt-5.6-luna", "why": None, "effort": "high"}}}
        tr, _ = client([(200, answer)])

        result = tr.translate_file('{"home": "Home"}', "es", filename="en.json")

        self.assertEqual(2, result.corrected)
        self.assertEqual("high", result.effort)

    def test_bytes_go_up_as_a_multipart_upload_untouched(self):
        tr, transport = client([(200, self.ANSWER)])
        # UTF-16 with its byte order mark: not valid UTF-8, and it must not be decoded.
        strings = '"home" = "Inicio";\n'.encode("utf-16")

        tr.translate_file(strings, ["es", "de"], filename="Localizable.strings", source="en",
                          anonymize=False, terms={"es": {"cart": "cesta"}, "Acme": "Acme"}, effort="low")

        call = transport.calls[0]
        self.assertIsNone(call["body"])
        self.assertTrue(call["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertEqual([
            ("targets[]", None, b"es"),
            ("targets[]", None, b"de"),
            ("source", None, b"en"),
            ("terms[es][cart]", None, b"cesta"),
            ("terms[Acme]", None, b"Acme"),
            ("anonymize", None, b"0"),
            ("effort", None, b"low"),
            ("file", "Localizable.strings", strings),
        ], call["form"])
        self.assertIsNotNone(call["key"])

    def test_a_boolean_true_is_one(self):
        tr, transport = client([(200, self.ANSWER)])

        tr.translate_file(b"msgid", "es", format="po", anonymize=True)

        self.assertEqual([
            ("target", None, b"es"),
            ("format", None, b"po"),
            ("anonymize", None, b"1"),
            ("file", "file", b"msgid"),
        ], transport.calls[0]["form"])

    def test_a_path_is_uploaded_and_named_after_itself(self):
        tr, transport = client([(200, self.ANSWER)])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "en.json"
            path.write_bytes(b'{"home": "Home"}\r\n')

            tr.translate_file(path, "es", effort="low")

        self.assertEqual([
            ("target", None, b"es"),
            ("effort", None, b"low"),
            ("file", "en.json", b'{"home": "Home"}\r\n'),
        ], transport.calls[0]["form"])

    def test_a_retried_upload_is_the_same_body_under_the_same_key(self):
        tr, transport = client([(503, {"error": {"code": "translation_unavailable"}}), (200, self.ANSWER)])

        tr.translate_file(b"\x00\xff", "es", filename="en.json")

        first, second = transport.calls
        self.assertEqual(first["raw"], second["raw"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(first["headers"]["Content-Type"], second["headers"]["Content-Type"])

    def test_a_plain_string_is_content_never_a_path(self):
        tr, transport = client([(200, self.ANSWER)])

        tr.translate_file("lang/en.json", "es", format="text")

        self.assertEqual("lang/en.json", transport.calls[0]["body"]["content"])
        self.assertEqual("application/json", transport.calls[0]["headers"]["Content-Type"])


class ImageTest(unittest.TestCase):
    ANSWER = {
        "id": "tr_01k8",
        "reference": None,
        "source": {"language": "ja"},
        "targets": ["en"],
        "blocks": [{"text": "出口", "box": {"x": 0.12, "y": 0.08, "width": 0.3, "height": 0.1}, "translations": {"en": "Exit"}}],
        "usage": {"characters": 2, "blocks": 1, "model": {"read": True, "name": "gpt-5.6-luna", "why": None, "effort": "high"}, "tokens": {"input": 1, "output": 1}},
        "credits": {"charged": 13, "remaining": 100},
        "mode": "live",
        "took_ms": 2000,
    }

    def sent(self, image, **options):
        tr, transport = client([(200, self.ANSWER)])
        result = tr.translate_image(image, "en", **options)
        return result, transport.calls[0]["body"]

    def test_an_address_goes_out_as_a_url(self):
        result, body = self.sent("https://cdn.example.test/sign.png", context="a street sign")

        self.assertEqual({"url": "https://cdn.example.test/sign.png", "target": "en", "context": "a street sign"}, body)
        self.assertEqual("ja", result.source)
        self.assertEqual("Exit", result.blocks[0]["translations"]["en"])
        self.assertEqual(13, result.charged)
        self.assertEqual("high", result.effort)

    def test_the_scheme_is_matched_without_case(self):
        _, body = self.sent("HTTPS://cdn.example.test/sign.png")

        self.assertIn("url", body)

    def test_bytes_go_out_as_base64(self):
        _, body = self.sent(b"\x89PNG\r\n")

        self.assertEqual(base64.b64encode(b"\x89PNG\r\n").decode(), body["data"])
        self.assertNotIn("url", body)

    def test_a_data_uri_is_left_alone(self):
        uri = "data:image/png;base64,iVBORw0KGgo="

        _, body = self.sent(uri)

        self.assertEqual(uri, body["data"])

    def test_base64_is_not_encoded_twice(self):
        encoded = base64.b64encode(b"\x89PNG\r\n" * 40).decode()
        wrapped = "\n".join(encoded[i:i + 76] for i in range(0, len(encoded), 76))

        _, body = self.sent(wrapped)

        self.assertEqual(encoded, body["data"])

    def test_url_safe_unpadded_base64_goes_out_standard(self):
        raw = b"\xfb\xff\xfe" * 7 + b"\x01"
        urlsafe = base64.urlsafe_b64encode(raw).decode().rstrip("=")

        _, body = self.sent(urlsafe)

        self.assertEqual(raw, base64.b64decode(body["data"], validate=True))

    def test_a_path_is_read_as_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sign.png"
            path.write_bytes(b"\x89PNG\r\n")

            _, body = self.sent(path)

        self.assertEqual(base64.b64encode(b"\x89PNG\r\n").decode(), body["data"])

    def test_an_image_takes_no_format_or_anonymize(self):
        tr, _ = client([])

        with self.assertRaises(TypeError):
            tr.translate_image("https://x.test/a.png", "en", anonymize=True)


class DetectTest(unittest.TestCase):
    def test_it_reads_the_language(self):
        tr, transport = client([(200, {
            "id": "tr_01k9", "reference": None,
            "language": {"code": "pt", "name": "Portuguese", "native": "Português", "rtl": False},
            "confidence": 0.97, "by": "model", **USAGE,
        })])

        detection = tr.detect("Obrigado pela ajuda")

        self.assertEqual("pt", detection.code)
        self.assertEqual("Português", detection.language["native"])
        self.assertEqual(0.97, detection.confidence)
        self.assertEqual("model", detection.by)
        self.assertEqual({"text": "Obrigado pela ajuda"}, transport.calls[0]["body"])
        self.assertEqual("https://example.test/api/v1/detect", transport.calls[0]["url"])

    def test_no_language_is_none(self):
        tr, _ = client([(200, {"id": "tr_1", "language": None, "confidence": 0.0, "by": "test_key", **USAGE})])

        detection = tr.detect("hello")

        self.assertIsNone(detection.language)
        self.assertIsNone(detection.code)


class BatchTest(unittest.TestCase):
    def test_it_queues_and_reads_the_summary(self):
        tr, transport = client([(202, BATCH)])

        batch = tr.batch(["a", "b", "c"], ["es", "fr"], glossary="shop", effort="medium")

        self.assertEqual("bat_01k6", batch.id)
        self.assertEqual("queued", batch.status)
        self.assertEqual(3, batch.total)
        self.assertEqual(40, batch.credits_estimated)
        self.assertEqual([], batch.results)
        self.assertEqual(
            {"texts": ["a", "b", "c"], "targets": ["es", "fr"], "glossary": "shop", "effort": "medium"},
            transport.calls[0]["body"],
        )
        self.assertEqual("https://example.test/api/v1/batches", transport.calls[0]["url"])

    def test_a_page_separates_translations_from_failures(self):
        tr, transport = client([(200, {
            **BATCH,
            "status": "completed",
            "done": 2,
            "failed": 1,
            "results": [
                {"index": 0, "translations": {"es": "Hola"}, "intact": True},
                {"index": 7, "error": {"code": "translation_unavailable", "message": "..."}},
            ],
            "next_after": 7,
        })])

        batch = tr.batch_status("bat_01k6", cursor="-1", limit=2)

        self.assertEqual({0: {"es": "Hola"}}, batch.translations)
        self.assertEqual("translation_unavailable", batch.failures[7]["code"])
        self.assertEqual("7", batch.next)
        self.assertIsInstance(batch.page, Page)
        self.assertEqual("7", batch.page.next)
        self.assertEqual(2, len(batch.page.items))
        self.assertEqual("https://example.test/api/v1/batches/bat_01k6?after=-1&limit=2", transport.calls[0]["url"])
        self.assertIsNone(transport.calls[0]["key"])

    def test_the_last_page_has_no_more(self):
        tr, _ = client([(200, {**BATCH, "results": [], "next_after": None})])

        self.assertIsNone(tr.batch_status("bat_01k6").next)

    def test_it_lists_the_recent_ones(self):
        tr, transport = client([(200, {"data": [BATCH, {**BATCH, "id": "bat_2"}]})])

        batches = tr.batches(limit=2)

        self.assertEqual(["bat_01k6", "bat_2"], [b.id for b in batches])
        self.assertEqual("https://example.test/api/v1/batches?limit=2", transport.calls[0]["url"])


class GlossaryTest(unittest.TestCase):
    GLOSSARY = {
        "slug": "shop",
        "name": "Shop",
        "source": "en",
        "target": "es",
        "entries": [{"term": "cart", "translation": "cesta"}],
        "updated_at": "2026-10-07T10:00:00+00:00",
    }

    def test_the_whole_lifecycle(self):
        tr, transport = client([
            (200, {"data": [{**self.GLOSSARY, "entries": 1}]}),
            (201, self.GLOSSARY),
            (200, self.GLOSSARY),
            (200, self.GLOSSARY),
            (200, {"deleted": True}),
        ])

        listed = tr.glossaries()
        created = tr.create_glossary("Shop", {"cart": "cesta"}, source="en", target="es")
        read = tr.glossary("shop")
        tr.update_glossary("shop", name="Store", target=None)
        deleted = tr.delete_glossary("shop")

        self.assertEqual(1, listed[0].entry_count)
        self.assertEqual([], listed[0].entries)
        self.assertEqual("shop", created.slug)
        self.assertEqual({"cart": "cesta"}, read.terms)
        self.assertEqual(1, read.entry_count)
        self.assertEqual({"deleted": True}, deleted)

        calls = [(c["method"], c["url"].replace("https://example.test/api", ""), c["body"]) for c in transport.calls]
        self.assertEqual(
            [
                ("GET", "/v1/glossaries", None),
                ("POST", "/v1/glossaries", {"name": "Shop", "source": "en", "target": "es", "entries": {"cart": "cesta"}}),
                ("GET", "/v1/glossaries/shop", None),
                ("PUT", "/v1/glossaries/shop", {"name": "Store", "target": None}),
                ("DELETE", "/v1/glossaries/shop", None),
            ],
            calls,
        )

    def test_an_update_sends_only_what_was_passed(self):
        tr, transport = client([(200, self.GLOSSARY)])

        tr.update_glossary("shop", entries=[{"term": "cart", "translation": "carrito"}])

        self.assertEqual({"entries": [{"term": "cart", "translation": "carrito"}]}, transport.calls[0]["body"])
        self.assertEqual("UNSET", repr(UNSET))

    def test_a_slug_cannot_walk_to_another_endpoint(self):
        tr, transport = client([(200, self.GLOSSARY)])

        tr.glossary("../keys")

        self.assertTrue(transport.calls[0]["url"].endswith("/v1/glossaries/..%2Fkeys"))


class CorrectionTest(unittest.TestCase):
    CORRECTION = {"id": 42, "text": "Add to cart", "target": "es", "translation": "Añadir a la cesta", "updated_at": "2026-10-07T10:00:00+00:00"}

    def test_save_list_and_delete(self):
        tr, transport = client([
            (201, self.CORRECTION),
            (200, {"data": [self.CORRECTION], "next_cursor": "eyJpZCI6NDJ9"}),
            (200, {"deleted": True}),
        ])

        saved = tr.correct("Add to cart", "es", "Añadir a la cesta")
        page = tr.corrections(target="es", limit=1)
        tr.delete_correction(saved.id)

        self.assertEqual(42, saved.id)
        self.assertEqual("Añadir a la cesta", saved.translation)
        self.assertEqual(1, len(page))
        self.assertEqual("Add to cart", next(iter(page)).text)
        self.assertIsInstance(page, Page)
        self.assertEqual("eyJpZCI6NDJ9", page.next)

        self.assertEqual("PUT", transport.calls[0]["method"])
        self.assertEqual({"text": "Add to cart", "target": "es", "translation": "Añadir a la cesta"}, transport.calls[0]["body"])
        self.assertTrue(transport.calls[1]["url"].endswith("/v1/corrections?target=es&limit=1"))
        self.assertEqual("DELETE", transport.calls[2]["method"])
        self.assertTrue(transport.calls[2]["url"].endswith("/v1/corrections/42"))


class AccountTest(unittest.TestCase):
    RECORD = {
        "id": "tr_01k6", "reference": "release-42", "kind": "text", "channel": "api",
        "source": "en", "targets": ["es", "de"], "format": "html", "characters": 1840,
        "segments": 24, "cached": 20, "anonymized": 0, "model": "gpt-5.6-luna",
        "credits": 9, "took_ms": 1290, "created_at": "2026-10-03T09:12:44+00:00",
    }

    def test_the_history(self):
        tr, transport = client([
            (200, {"data": [self.RECORD], "next_cursor": None}),
            (200, self.RECORD),
        ])

        page = tr.translations(kind="text", reference="release-42", cursor="abc")
        record = tr.translation_record("tr_01k6")

        self.assertIsNone(page.next)
        self.assertEqual(["es", "de"], page.items[0].targets)
        self.assertEqual(9, record.credits)
        self.assertEqual(20, record.cached)
        self.assertEqual("gpt-5.6-luna", record.model)
        self.assertTrue(transport.calls[0]["url"].endswith("/v1/translations?kind=text&reference=release-42&cursor=abc"))
        self.assertTrue(transport.calls[1]["url"].endswith("/v1/translations/tr_01k6"))

    def test_the_free_reads(self):
        tr, transport = client([
            (200, {"data": [{"code": "es", "name": "Spanish", "native": "Español", "rtl": False}]}),
            (200, {"plan": "free", "credits": {"remaining": 1840, "renews_at": None}}),
            (200, {"data": [{"id": 12, "name": "Production", "mode": "live", "prefix": "tr_live_aB3x"}]}),
            (200, {"ok": True, "mode": "test", "key": "tr_test_abcd"}),
        ])

        self.assertEqual("Español", tr.languages()[0]["native"])
        self.assertEqual("free", tr.usage()["plan"])
        self.assertEqual(12, tr.keys()[0]["id"])
        self.assertTrue(tr.ping()["ok"])

        self.assertEqual(["/v1/languages", "/v1/usage", "/v1/keys", "/v1/ping"],
                         [c["url"].replace("https://example.test/api", "") for c in transport.calls])
        self.assertTrue(all(c["method"] == "GET" and c["key"] is None for c in transport.calls))

    def test_revoking_a_key(self):
        tr, transport = client([(200, {"revoked": True})])

        self.assertEqual({"revoked": True}, tr.revoke_key(12))
        self.assertEqual("DELETE", transport.calls[0]["method"])
        self.assertTrue(transport.calls[0]["url"].endswith("/v1/keys/12"))
        self.assertTrue(transport.calls[0]["key"].startswith("py-"))

    def test_the_request_carries_the_key_and_the_client(self):
        from translatorio import VERSION

        tr, transport = client([(200, {"ok": True})])

        tr.ping()

        headers = transport.calls[0]["headers"]
        self.assertEqual("Bearer tr_test_key", headers["Authorization"])
        self.assertEqual(f"translatorio-python/{VERSION}", headers["User-Agent"])
        self.assertEqual("application/json", headers["Accept"])

    def test_the_default_address(self):
        transport = FakeTransport([(200, {"ok": True})])

        Client("tr_live_x", transport=transport).ping()

        self.assertEqual("https://translatorio.com/api/v1/ping", transport.calls[0]["url"])


class ErrorTest(unittest.TestCase):
    def raised(self, status, payload, headers=None):
        response = (status, payload, headers) if headers else (status, payload)
        tr, transport = client([response, response, response], retries=0)

        with self.assertRaises(TranslatorioError) as caught:
            tr.translate("Hi", "es")

        return caught.exception, transport

    def test_a_bad_key(self):
        error, _ = self.raised(401, {"error": {"code": "invalid_api_key", "message": "That key is not recognised."}})

        self.assertIsInstance(error, AuthenticationError)
        self.assertEqual("invalid_api_key", error.code)
        self.assertEqual("That key is not recognised.", error.message)
        self.assertEqual(401, error.status)

    def test_a_quota_carries_the_numbers(self):
        error, _ = self.raised(402, {
            "error": {"code": "quota_exhausted", "message": "..."},
            "credits": {"remaining": 3, "required": 8, "queued": 40, "renews_at": "2026-11-01T00:00:00+00:00"},
        })

        self.assertIsInstance(error, QuotaExhausted)
        self.assertEqual(3, error.remaining)
        self.assertEqual(8, error.required)
        self.assertEqual(40, error.queued)
        self.assertEqual("2026-11-01T00:00:00+00:00", error.renews_at)

    def test_a_validation_failure_carries_its_fields(self):
        error, _ = self.raised(422, {"error": {"code": "invalid_request", "message": "The texts field must be an array.", "fields": {"texts": ["The texts field must be an array."]}}})

        self.assertIsInstance(error, InvalidRequest)
        self.assertEqual({"texts": ["The texts field must be an array."]}, error.fields)

    def test_a_refusal_about_one_field_names_it(self):
        error, _ = self.raised(422, {"error": {"code": "unsupported_language", "message": "`klingon` is not a language we translate.", "fields": {"targets": ["`klingon` is not a language we translate."]}}})

        self.assertEqual("unsupported_language", error.code)
        self.assertEqual({"targets": ["`klingon` is not a language we translate."]}, error.fields)
        self.assertFalse(hasattr(error, "field"))

    def test_a_refusal_about_the_whole_request_has_no_fields(self):
        error, _ = self.raised(422, {"error": {"code": "invalid_request", "message": "Send the file as exactly one of `file` or `content`."}})

        self.assertEqual({}, error.fields)

    def test_credits_inside_the_error_are_not_read(self):
        error, _ = self.raised(402, {"error": {"code": "quota_exhausted", "credits": {"remaining": 3, "required": 8}}})

        self.assertEqual(0, error.remaining)
        self.assertEqual(0, error.required)

    def test_not_found(self):
        error, _ = self.raised(404, {"error": {"code": "not_found", "message": "There is no such endpoint."}})

        self.assertIsInstance(error, NotFound)

    def test_a_rate_limit_carries_the_wait(self):
        error, _ = self.raised(429, {"error": {"code": "rate_limited", "message": "..."}}, {"Retry-After": "12"})

        self.assertIsInstance(error, RateLimited)
        self.assertEqual(12, error.retry_after)

    def test_the_wait_in_the_body_comes_first(self):
        error, _ = self.raised(429, {"error": {"code": "rate_limited", "message": "...", "retry_after": 30}}, {"Retry-After": "12"})

        self.assertEqual(30, error.retry_after)

    def test_the_wait_in_the_body_is_enough(self):
        error, _ = self.raised(429, {"error": {"code": "rate_limited", "message": "...", "retry_after": 7}})

        self.assertEqual(7, error.retry_after)

    def test_a_rate_limit_without_a_header_waits_one_second(self):
        error, _ = self.raised(429, {"error": {"code": "rate_limited"}})

        self.assertEqual(1, error.retry_after)

    def test_an_outage_is_a_server_error(self):
        error, _ = self.raised(503, {"error": {"code": "translation_unavailable", "message": "Nothing was charged; retry in a moment."}})

        self.assertIsInstance(error, ServerError)
        self.assertEqual("translation_unavailable", error.code)
        self.assertTrue(error.retryable)

    def test_an_unreachable_service_is_status_zero(self):
        tr, _ = client([(0, ServerError("Could not reach Translatorio: refused"))], retries=0)

        with self.assertRaises(ServerError) as caught:
            tr.translate("Hi", "es")

        self.assertEqual(0, caught.exception.status)

    def test_an_error_page_that_is_not_json_is_still_typed(self):
        error, _ = self.raised(502, "<html>Bad gateway</html>")

        self.assertIsInstance(error, ServerError)
        self.assertEqual(502, error.status)


class RetryTest(unittest.TestCase):
    def test_it_retries_a_rate_limit(self):
        tr, transport = client([(429, {"error": {"code": "rate_limited"}}), (200, TRANSLATION)])

        self.assertEqual("Añadir a la cesta", tr.translate("Add to cart", "es").translation)
        self.assertEqual(2, len(transport.calls))

    def test_a_retry_is_the_same_request(self):
        """The pairing that makes automatic retries safe: same key, so one charge."""
        tr, transport = client([(500, {}), (503, {"error": {"code": "translation_unavailable"}}), (200, TRANSLATION)])

        tr.translate("Add to cart", "es")

        self.assertEqual(3, len(transport.calls))
        self.assertEqual(1, len({c["key"] for c in transport.calls}))
        self.assertTrue(transport.calls[0]["key"].startswith("py-"))

    def test_two_calls_are_two_keys(self):
        tr, transport = client([(200, TRANSLATION), (200, TRANSLATION)])

        tr.translate("one", "es")
        tr.translate("two", "es")

        self.assertNotEqual(transport.calls[0]["key"], transport.calls[1]["key"])

    def test_your_own_key_is_used(self):
        tr, transport = client([(500, {}), (200, TRANSLATION)])

        tr.translate("one", "es", idempotency_key="release-42-es")

        self.assertEqual(["release-42-es", "release-42-es"], [c["key"] for c in transport.calls])

    def test_every_write_carries_a_key_and_no_read_does(self):
        tr, transport = client([(200, {"slug": "s"}), (200, {"deleted": True}), (200, {"id": 1}), (200, {"data": []})])

        tr.update_glossary("s", name="S")
        tr.delete_glossary("s")
        tr.correct("a", "es", "b")
        tr.glossaries()

        self.assertEqual([True, True, True, False], [c["key"] is not None for c in transport.calls])

    def test_it_never_retries_a_quota(self):
        """402 means come back with a bigger plan. Retrying it hammers forever."""
        tr, transport = client([
            (402, {"error": {"code": "quota_exhausted"}, "credits": {"remaining": 2, "required": 13}}),
            (200, TRANSLATION),
        ])

        with self.assertRaises(QuotaExhausted) as caught:
            tr.translate_image("https://example.test/a.jpg", "en")

        self.assertEqual(1, len(transport.calls))
        self.assertFalse(caught.exception.retryable)

    def test_it_never_retries_another_refusal(self):
        for status in (400, 401, 403, 404, 405, 409, 422):
            tr, transport = client([(status, {"error": {"code": "x"}}), (200, TRANSLATION)])

            with self.assertRaises(TranslatorioError):
                tr.translate("Hi", "es")

            self.assertEqual(1, len(transport.calls), f"{status} was retried")

    def test_a_call_still_in_flight_is_waited_for(self):
        """The earlier attempt at this very call is still running: asking again is right."""
        tr, transport = client([
            (409, {"error": {"code": "idempotency_in_flight", "retry_after": 1}}, {"Retry-After": "1"}),
            (200, TRANSLATION),
        ])

        tr.translate("Hi", "es")

        self.assertEqual(2, len(transport.calls))

    def test_an_unreachable_service_is_retried(self):
        tr, transport = client([(0, ServerError("timed out")), (200, TRANSLATION)])

        tr.translate("Hi", "es")

        self.assertEqual(2, len(transport.calls))

    def test_it_gives_up_eventually(self):
        tr, transport = client([(500, {}), (500, {}), (500, {})], retries=2)

        with self.assertRaises(ServerError):
            tr.translate("Hi", "es")

        self.assertEqual(3, len(transport.calls))


class WaitTest(unittest.TestCase):
    def waiting(self, responses, **kwargs):
        waited = []
        transport = FakeTransport(responses)
        tr = Client("tr_test_key", "https://example.test/api", transport=transport, sleep=waited.append, **kwargs)

        return tr, waited

    def test_it_waits_as_long_as_the_service_asked(self):
        tr, waited = self.waiting([(429, {}, {"retry-after": "7"}), (200, TRANSLATION)])

        tr.translate("Hi", "es")

        self.assertEqual([7], waited)

    def test_the_body_says_the_wait_before_the_header(self):
        tr, waited = self.waiting([
            (429, {"error": {"code": "rate_limited", "retry_after": 9}}, {"Retry-After": "3"}),
            (409, {"error": {"code": "idempotency_in_flight", "retry_after": 2}}),
            (200, TRANSLATION),
        ])

        tr.translate("Hi", "es")

        self.assertEqual([9, 2], waited)

    def test_the_wait_is_bounded(self):
        tr, waited = self.waiting([(429, {}, {"Retry-After": "86400"}), (200, TRANSLATION)])

        tr.translate("Hi", "es")

        self.assertEqual([30], waited, "A day is not a retry, it is a hang.")

    def test_the_bound_is_yours_to_set(self):
        tr, waited = self.waiting([(429, {}, {"Retry-After": "45"}), (200, TRANSLATION)], max_wait=5)

        tr.translate("Hi", "es")

        self.assertEqual([5], waited)

    def test_without_a_header_the_wait_grows(self):
        tr, waited = self.waiting([(500, {}), (429, {}), (200, TRANSLATION)])

        tr.translate("Hi", "es")

        self.assertEqual([2, 4], waited)

    def test_a_retry_after_that_is_not_a_number_is_ignored(self):
        tr, waited = self.waiting([(503, {}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), (200, TRANSLATION)])

        tr.translate("Hi", "es")

        self.assertEqual([2], waited)


if __name__ == "__main__":
    unittest.main()
