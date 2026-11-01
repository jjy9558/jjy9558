import unittest

from steam_buddy.steam import SteamClient, SteamError
from tests.fakes import FakeSteam, details_payload


class SteamClientTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSteam()
        self.client = SteamClient(fetch=self.fake)

    def test_search(self):
        self.fake.search_items = [{"id": 1, "name": "A", "type": "app"}, {"id": 2, "name": "B"}]
        self.assertEqual(self.client.search("x"), [{"appid": 1, "name": "A"}, {"appid": 2, "name": "B"}])

    def test_app_details(self):
        self.fake.details.update(details_payload(5, "Game", price=19800, initial=33000, discount=40,
                                                 langs="English, Korean<strong>*</strong>", early=True))
        d = self.client.app_details(5)
        self.assertEqual(d["price"], 19800)
        self.assertEqual(d["initial_price"], 33000)
        self.assertEqual(d["discount"], 40)
        self.assertEqual(d["korean"], "자막+음성")
        self.assertTrue(d["early_access"])

    def test_free_game_without_price(self):
        self.fake.details.update(details_payload(6, "Free", free=True))
        d = self.client.app_details(6)
        self.assertTrue(d["is_free"])
        self.assertEqual(d["price"], 0)

    def test_missing_app(self):
        with self.assertRaises(SteamError):
            self.client.app_details(999)

    def test_reviews(self):
        self.fake.reviews[(5, "all")] = (90, 10)
        r = self.client.reviews(5)
        self.assertEqual(r["total"], 100)
        self.assertAlmostEqual(r["ratio"], 0.9)

    def test_reviews_empty(self):
        self.assertIsNone(self.client.reviews(5)["ratio"])

    def test_last_update_picks_latest(self):
        self.fake.news[5] = [100, 300, 200]
        self.assertEqual(self.client.last_update(5)["date"], 300)
        self.assertIsNone(self.client.last_update(7))

    def test_owned_games(self):
        self.fake.owned = [(1, "A", 30), (2, "B", 0)]
        self.assertEqual(self.client.owned_games("k", "s"),
                         [{"appid": 1, "name": "A", "minutes": 30}, {"appid": 2, "name": "B", "minutes": 0}])


if __name__ == "__main__":
    unittest.main()
