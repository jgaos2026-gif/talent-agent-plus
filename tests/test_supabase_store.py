"""Supabase store adapter tests — stdlib only, no network.

The HTTP layer is mocked (urllib.request.urlopen is patched), so these
tests prove the adapter's config detection, fail-safe behavior, RPC
shapes, and trial-claim error mapping without touching any live project.
No real key is used anywhere here — the "keys" below are fake sentinels.
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from supabase_store import (  # noqa: E402
    SupabaseStore, SupabaseError, TrialAlreadyClaimed, ArtistNotFound)

FAKE_URL = "https://hnczbvhlhzfwscfpltoe.supabase.co"
FAKE_KEY = "test-sentinel-not-a-real-key"


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, message):
    err = urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(
        json.dumps({"message": message}).encode()))
    return err


class TestConfig(unittest.TestCase):
    def test_unset_env_is_not_configured_and_never_calls_network(self):
        with patch.dict(os.environ, {}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)
            self.assertFalse(store.enabled)
            with patch("supabase_store.urllib.request.urlopen") as urlopen:
                self.assertFalse(store.initialize())
                urlopen.assert_not_called()

    def test_url_without_key_is_not_configured(self):
        with patch.dict(os.environ, {"SUPABASE_URL": FAKE_URL}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)
            self.assertFalse(store.initialize())

    def test_key_without_url_is_not_configured(self):
        with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": FAKE_KEY}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)
            self.assertFalse(store.initialize())

    def test_non_https_url_is_not_configured(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "http://example.com",
                                     "SUPABASE_SERVICE_ROLE_KEY": FAKE_KEY}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)

    def test_both_set_ping_ok_enables(self):
        store = SupabaseStore(FAKE_URL, FAKE_KEY)
        self.assertTrue(store.configured)
        with patch("supabase_store.urllib.request.urlopen",
                   return_value=FakeResponse(200, "[]")) as urlopen:
            self.assertTrue(store.initialize())
            self.assertTrue(store.enabled)
            req = urlopen.call_args[0][0]
            self.assertEqual(req.full_url, FAKE_URL + "/rest/v1/")
            # the service-role key is sent as a header, never in the URL
            self.assertNotIn("test-sentinel", req.full_url)

    def test_both_set_ping_unreachable_fails_safe(self):
        store = SupabaseStore(FAKE_URL, FAKE_KEY)
        with patch("supabase_store.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            self.assertFalse(store.initialize())
            self.assertFalse(store.enabled)

    def test_both_set_ping_401_fails_safe(self):
        store = SupabaseStore(FAKE_URL, "test-wrong-sentinel")
        with patch("supabase_store.urllib.request.urlopen",
                   side_effect=http_error(FAKE_URL, 401, "Invalid API key")):
            self.assertFalse(store.initialize())
            self.assertFalse(store.enabled)


class TestRpc(unittest.TestCase):
    def setUp(self):
        self.store = SupabaseStore(FAKE_URL, FAKE_KEY)
        self.store.enabled = True

    def test_rpc_posts_to_public_wrapper_with_service_key_headers(self):
        def fake_open(req, **kwargs):
            self.assertEqual(req.full_url, FAKE_URL + "/rest/v1/rpc/tap_append_evidence")
            self.assertEqual(req.get_header("Apikey"), FAKE_KEY)
            self.assertEqual(req.get_header("Authorization"), "Bearer " + FAKE_KEY)
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_event_type"], "TEST")
            return FakeResponse(200, '"abc123hash"')
        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            out = self.store.append_evidence("00000000-0000-0000-0000-000000000001",
                                             None, "TEST", {"a": 1})
            self.assertEqual(out, "abc123hash")

    def test_rpc_void_function_returns_none_on_204(self):
        with patch("supabase_store.urllib.request.urlopen",
                   return_value=FakeResponse(204, "")):
            out = self.store.rpc("tap_claim_trial", {"p_email": "a@example.com",
                                                     "p_artist_id": "00000000-0000-0000-0000-000000000001"})
            self.assertIsNone(out)

    def test_rpc_http_error_never_leaks_the_key(self):
        with patch("supabase_store.urllib.request.urlopen",
                   side_effect=http_error(FAKE_URL, 401, "Invalid API key")):
            with self.assertRaises(SupabaseError) as ctx:
                self.store.rpc("tap_verify_evidence_chain", {})
            self.assertNotIn(FAKE_KEY, str(ctx.exception))
            self.assertIn("401", str(ctx.exception))

    def test_verify_evidence_chain_true(self):
        with patch("supabase_store.urllib.request.urlopen",
                   return_value=FakeResponse(200, "true")):
            self.assertTrue(self.store.verify_evidence_chain())

    def test_claim_trial_resolves_artist_by_email_then_claims(self):
        calls = []

        def fake_open(req, **kwargs):
            calls.append(req)
            if req.full_url.startswith(FAKE_URL + "/rest/v1/artists"):
                return FakeResponse(200, json.dumps(
                    [{"id": "11111111-1111-1111-1111-111111111111",
                      "email": "artist@example.com"}]))
            if req.full_url.endswith("/rpc/tap_claim_trial"):
                body = json.loads(req.data.decode())
                self.assertEqual(body["p_email"], "artist@example.com")
                self.assertEqual(body["p_artist_id"], "11111111-1111-1111-1111-111111111111")
                return FakeResponse(204, "")
            raise AssertionError("unexpected " + req.full_url)

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            out = self.store.claim_trial("Artist@Example.com")
            self.assertEqual(out["trial"], "claimed")
            self.assertEqual(out["email"], "artist@example.com")
            # the table read used the tap profile, not public
            # (urllib capitalizes header names: "Accept-profile")
            self.assertEqual(calls[0].get_header("Accept-profile"), "tap")

    def test_claim_trial_already_claimed_maps_to_typed_error(self):
        def fake_open(req, **kwargs):
            if req.full_url.startswith(FAKE_URL + "/rest/v1/artists"):
                return FakeResponse(200, json.dumps(
                    [{"id": "11111111-1111-1111-1111-111111111111",
                      "email": "artist@example.com"}]))
            raise http_error(req.full_url, 400, "trial_already_claimed")

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            with self.assertRaises(TrialAlreadyClaimed):
                self.store.claim_trial("artist@example.com")

    def test_claim_trial_unknown_artist_is_a_clear_error(self):
        with patch("supabase_store.urllib.request.urlopen",
                   return_value=FakeResponse(200, "[]")):
            with self.assertRaises(ArtistNotFound):
                self.store.claim_trial("nobody@example.com")

    def test_claim_trial_rejects_a_bad_email_without_network(self):
        with patch("supabase_store.urllib.request.urlopen") as urlopen:
            with self.assertRaises(SupabaseError):
                self.store.claim_trial("not-an-email")
            urlopen.assert_not_called()

    def test_record_cleared_payment_sends_the_full_rpc_shape(self):
        def fake_open(req, **kwargs):
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_stripe_object_id"], "pi_test123")
            self.assertEqual(body["p_stage"], "stripe-cleared")
            self.assertEqual(body["p_amount_cents"], 100000)
            self.assertIsNone(body["p_booking_id"])
            return FakeResponse(200, '"22222222-2222-2222-2222-222222222222"')

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            pid = self.store.record_cleared_payment(
                "11111111-1111-1111-1111-111111111111", "pi_test123",
                "stripe-cleared", 100000, "owner")
            self.assertEqual(pid, "22222222-2222-2222-2222-222222222222")


if __name__ == "__main__":
    unittest.main()
