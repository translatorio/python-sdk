"""Failing closed: nothing that is not an answer is ever read as one.

A translation client that turns "I could not read the answer" into an empty result
publishes nothing where a translation should be, or a proxy's login page where it should
be. Only a 2xx whose body is a JSON object is an answer. The real transport is tested
against a server on the loopback interface, because the bugs here live in its except
clauses and a fake one would answer for the fake.
"""

import http.client
import http.server
import sys
import threading
import time
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translatorio import Client, ServerError
from translatorio.client import UrllibTransport


class RawTransport:
    """Answers with raw strings, because the bugs here live in what is NOT JSON."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def send(self, method, url, headers, body):
        self.calls += 1
        return self.responses.pop(0)


def raw_client(responses, **kwargs):
    transport = RawTransport(responses)
    return Client("tr_test_key", "https://example.test/api", transport=transport, sleep=lambda s: None, **kwargs), transport


class FailClosedTest(unittest.TestCase):
    def assert_no_answer(self, response):
        tr, _ = raw_client([response], retries=0)

        with self.assertRaises(ServerError) as caught:
            tr.translate("Hi", "es")

        self.assertTrue(caught.exception.retryable)

        return caught.exception

    def test_a_redirect_is_not_an_answer(self):
        self.assertEqual(302, self.assert_no_answer((302, "")).status)

    def test_an_empty_success_is_not_an_answer(self):
        self.assertEqual(200, self.assert_no_answer((200, "")).status)

    def test_html_is_not_an_answer(self):
        self.assert_no_answer((200, "<html>Sign in to the proxy</html>"))

    def test_json_that_is_not_an_object_is_not_an_answer(self):
        self.assert_no_answer((200, "[]"))
        self.assert_no_answer((200, "null"))
        self.assert_no_answer((200, '"ok"'))

    def test_an_unreadable_answer_is_retried(self):
        tr, transport = raw_client([(200, "<html>"), (200, '{"id": "tr_1", "translation": "Hola"}')])

        self.assertEqual("Hola", tr.translate("Hi", "es").translation)
        self.assertEqual(2, transport.calls)

    def test_a_created_answer_is_an_answer(self):
        tr, _ = raw_client([(201, '{"slug": "shop"}')])

        self.assertEqual("shop", tr.create_glossary("Shop").slug)


def serve(handler):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server


class RealTransportTest(unittest.TestCase):
    def test_it_does_not_follow_a_redirect(self):
        """Urllib follows a POST 302 as a GET, carrying the Authorization header with it."""
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                hits.append(self.path)
                body = b'{"translation": "Hola"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = serve(Handler)

        try:
            url = f"http://127.0.0.1:{server.server_port}/api/v1/translate"
            status, _, _ = UrllibTransport(timeout=5).send("POST", url, {"Authorization": "Bearer k"}, b"{}")
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(302, status)
        self.assertEqual(["/api/v1/translate"], hits)

    def test_it_hands_back_the_headers(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"error": {"code": "rate_limited"}}'
                self.send_response(429)
                self.send_header("Retry-After", "9")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = serve(Handler)

        try:
            url = f"http://127.0.0.1:{server.server_port}/api/v1/ping"
            status, body, headers = UrllibTransport(timeout=5).send("GET", url, {}, None)
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(429, status)
        self.assertIn("rate_limited", body)
        self.assertEqual("9", headers["Retry-After"])

    def test_a_stalled_body_times_out_as_a_server_error(self):
        """The timeout covers the read, not only the connection."""
        release = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b'{"transl')
                self.wfile.flush()
                release.wait(5)

            def log_message(self, *args):
                pass

        server = serve(Handler)
        started = time.monotonic()

        try:
            url = f"http://127.0.0.1:{server.server_port}/api/v1/translate"

            with self.assertRaises(ServerError) as caught:
                UrllibTransport(timeout=0.3).send("POST", url, {}, b"{}")
        finally:
            release.set()
            server.shutdown()
            server.server_close()

        self.assertTrue(caught.exception.retryable)
        self.assertEqual(0, caught.exception.status)
        self.assertLess(time.monotonic() - started, 4)

    def test_failures_urllib_does_not_wrap_are_server_errors(self):
        class Stalling:
            def read(self, *args):
                raise TimeoutError("timed out")

            def close(self):
                pass

        for error in (
            TimeoutError("timed out"),
            http.client.IncompleteRead(b"{", 10),
            http.client.BadStatusLine("HTTP/9 banana"),
            urllib.error.URLError("refused"),
            urllib.error.HTTPError("https://example.test", 500, "oops", {}, Stalling()),
        ):
            transport = UrllibTransport()

            class Failing:
                def __init__(self, error):
                    self.error = error

                def open(self, *args, **kwargs):
                    raise self.error

            transport._opener = Failing(error)

            with self.assertRaises(ServerError, msg=repr(error)):
                transport.send("POST", "https://example.test/api/v1/translate", {}, b"{}")

    def test_a_transport_of_your_own_has_to_say_so(self):
        """Only ``TranslatorioError`` is retried, so your transport reports a dead connection
        as ``ServerError`` rather than letting the socket error through. A documented boundary.
        """

        class Leaking:
            def send(self, *args):
                raise TimeoutError("timed out")

        tr = Client("tr_test_key", transport=Leaking(), sleep=lambda s: None)

        with self.assertRaises(TimeoutError):
            tr.ping()


if __name__ == "__main__":
    unittest.main()
