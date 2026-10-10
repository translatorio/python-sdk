"""Checking that a delivery is ours, exactly as the API's own ``Signature::valid()`` does."""

import hashlib
import hmac
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translatorio import webhooks

BODY = '{"id":"evt_01k6","event":"batch.completed","created_at":"2026-10-07T10:00:00+00:00","data":{"id":"bat_01k6","status":"completed"}}'
SECRET = "whsec_" + "a" * 40


def sign(at=None, body=BODY, secret=SECRET):
    at = int(time.time()) if at is None else at
    digest = hmac.new(secret.encode(), f"{at}.".encode() + body.encode(), hashlib.sha256).hexdigest()

    return f"t={at},v1={digest}"


class WebhookTest(unittest.TestCase):
    def test_it_accepts_ours(self):
        self.assertTrue(webhooks.verify(BODY, sign(), SECRET))
        self.assertTrue(webhooks.verify(BODY.encode(), sign(), SECRET))

        event = webhooks.event(BODY, sign(), SECRET)
        self.assertEqual(webhooks.BATCH_COMPLETED, event["event"])
        self.assertEqual("bat_01k6", event["data"]["id"])

    def test_spaces_around_the_parts_are_allowed(self):
        at = int(time.time())
        header = sign(at).replace(",", " , ")

        self.assertTrue(webhooks.verify(BODY, header, SECRET))

    def test_a_wrong_secret_is_refused(self):
        self.assertFalse(webhooks.verify(BODY, sign(), "whsec_other"))

    def test_a_stale_delivery_is_refused(self):
        self.assertFalse(webhooks.verify(BODY, sign(int(time.time()) - 301), SECRET))
        self.assertTrue(webhooks.verify(BODY, sign(int(time.time()) - 290), SECRET))
        self.assertFalse(webhooks.verify(BODY, sign(int(time.time()) + 3600), SECRET))

    def test_the_window_is_yours_to_set(self):
        self.assertTrue(webhooks.verify(BODY, sign(int(time.time()) - 500), SECRET, tolerance=600))

    def test_a_changed_body_is_refused(self):
        self.assertFalse(webhooks.verify(BODY + " ", sign(), SECRET))
        self.assertFalse(webhooks.verify(BODY.replace("completed", "failed"), sign(), SECRET))

    def test_nonsense_is_refused(self):
        for header in ("nonsense", "", "t=,v1=", "t=abc,v1=00", f"t={int(time.time())}", "v1=00"):
            self.assertFalse(webhooks.verify(BODY, header, SECRET), header)

        self.assertIsNone(webhooks.event(BODY, "nonsense", SECRET))

    def test_a_signed_body_that_is_not_an_object_is_no_event(self):
        self.assertIsNone(webhooks.event("[1]", sign(body="[1]"), SECRET))

    def test_the_names_are_the_apis(self):
        self.assertEqual("X-Translatorio-Signature", webhooks.HEADER)
        self.assertEqual("X-Translatorio-Event", webhooks.EVENT_HEADER)
        self.assertEqual(("batch.completed", "credits.low", "quota.exhausted"), webhooks.EVENTS)


if __name__ == "__main__":
    unittest.main()
