"""App server tests — stdlib only. Starts the real server in a thread."""
import json
import os
import sys
import threading
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import ThreadingHTTPServer, Handler  # noqa: E402


def call(base, path, body=None, token=None):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + token} if token else {})},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        _, r = call(cls.base, "/api/setup-owner", {"password": "owner-pass-123"})
        assert r.get("ok")
        _, r = call(cls.base, "/api/login-owner", {"password": "owner-pass-123"})
        cls.owner = r["token"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_full_journey_and_negatives(self):
        b, owner = self.base, self.owner
        s, artist = call(b, "/api/artists", {"name": "Test Artist", "skills": ["music"],
                                              "location": "Peoria", "tier": "pro"}, owner)
        self.assertEqual(s, 200)
        aid = artist["artist_id"]
        call(b, "/api/artists/%s/password" % aid, {"password": "artist-pass-123"}, owner)
        s, art = call(b, "/api/login-artist", {"artist_id": aid, "password": "artist-pass-123"})
        self.assertEqual(s, 200)
        atok = art["token"]
        # unauthenticated booking must be refused
        s, err = call(b, "/api/bookings/offer", {"artist_id": aid, "opp_id": "opp-0001"})
        self.assertEqual(s, 400)
        # opportunity -> booking -> collect -> cut
        s, opp = call(b, "/api/opportunities", {"title": "Peoria Gig", "kind": "gig",
                                                 "skills": ["music"], "location": "Peoria",
                                                 "date": "2026-11-20", "pay_cents": 100000}, owner)
        self.assertEqual(s, 200, opp)
        s, bk = call(b, "/api/bookings/offer", {"artist_id": aid, "opp_id": opp["opp_id"]}, owner)
        self.assertEqual(s, 200, bk)
        bid = bk["booking_id"]
        s, _ = call(b, "/api/bookings/approve", {"booking_id": bid}, atok)
        self.assertEqual(s, 200)
        call(b, "/api/bookings/confirm", {"booking_id": bid}, owner)
        call(b, "/api/bookings/complete", {"booking_id": bid}, owner)
        s, receipt = call(b, "/api/bookings/collect", {"booking_id": bid, "cleared_cents": 100000,
                                                         "stage": "gig-pay-cleared"}, owner)
        self.assertEqual(s, 200, receipt)
        self.assertEqual(receipt["split"]["V8"], 20000)
        s, cut = call(b, "/api/bookings/pay-cut", {"receipt": receipt, "cut_cents": 4000,
                                                    "payee": "Demo Collaborator"}, owner)
        self.assertEqual(s, 200)
        self.assertEqual(cut["split"]["V8"], 16000)
        # double collect must be refused
        s, _ = call(b, "/api/bookings/collect", {"booking_id": bid, "cleared_cents": 100000,
                                                  "stage": "gig-pay-cleared"}, owner)
        self.assertEqual(s, 400)
        # calculator: uncleared refused, cut over V8 refused, $5000 exact
        s, _ = call(b, "/api/calc-11vt", {"cleared_cents": 500000, "cut_cents": 0, "cleared": False})
        self.assertEqual(s, 400)
        s, calc = call(b, "/api/calc-11vt", {"cleared_cents": 500000, "cut_cents": 0, "cleared": True})
        self.assertEqual(calc["total_check"], 500000)
        self.assertEqual(calc["split"]["V7"], 60000)
        s, _ = call(b, "/api/calc-11vt", {"cleared_cents": 100000, "cut_cents": 999999, "cleared": True})
        self.assertEqual(s, 400)
        # shop: product, order, clear must equal total, fulfill
        s, prod = call(b, "/api/shop/products", {"artist_id": aid, "name": "Test Tee",
                                                  "kind": "shirt", "price_cents": 2500, "stock": 10}, owner)
        s, order = call(b, "/api/shop/order", {"artist_id": aid, "product_id": prod["product_id"], "qty": 2}, owner)
        call(b, "/api/shop/confirm", {"order_id": order["order_id"]}, owner)
        s, _ = call(b, "/api/shop/clear", {"order_id": order["order_id"], "cleared_cents": 4999}, owner)
        self.assertEqual(s, 400)  # wrong amount, not the order total
        s, mrec = call(b, "/api/shop/clear", {"order_id": order["order_id"], "cleared_cents": 5000}, owner)
        self.assertEqual(s, 200)
        self.assertEqual(mrec["split"]["V8"], 1000)
        s, _ = call(b, "/api/shop/fulfill", {"order_id": order["order_id"], "shipping_note": "local"}, owner)
        self.assertEqual(s, 200)
        # campaign cap, draft only
        s, camp = call(b, "/api/campaigns", {"artist_id": aid, "title": "Push",
                                              "channels": ["social"], "budget_cap_cents": 10000}, owner)
        s, _ = call(b, "/api/campaigns/spend", {"campaign_id": camp["campaign_id"],
                                                 "amount_cents": 10001, "note": "over cap"}, owner)
        self.assertEqual(s, 400)
        s, _ = call(b, "/api/campaigns/spend", {"campaign_id": camp["campaign_id"],
                                                 "amount_cents": 2500, "note": "ok"}, owner)
        self.assertEqual(s, 200)
        # discover: publish, browse, connect, decide
        call(b, "/api/discover/publish", {"artist_id": aid, "bio": "Singer in Peoria."}, owner)
        s, found = call(b, "/api/discover/artists")
        self.assertTrue(any(a["artist_id"] == aid for a in found))
        s, conn = call(b, "/api/discover/connect", {"artist_id": aid, "seeker_name": "Booker",
                                                     "seeker_contact": "booker@example.com", "message": "Hi"})
        self.assertEqual(s, 200)
        s, dec = call(b, "/api/discover/decide", {"request_id": conn["request_id"], "accept": True}, owner)
        self.assertEqual(s, 200)


if __name__ == "__main__":
    unittest.main()
