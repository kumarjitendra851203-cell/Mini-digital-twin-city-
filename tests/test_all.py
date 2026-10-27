import os, tempfile, unittest
from sim.engine import Engine, L
from app import create_app


class EngineTests(unittest.TestCase):
    def test_empty_traffic_no_error(self):
        e = Engine(rate=0)
        for _ in range(30): e.step()
        m = e.metrics(); self.assertEqual((m["active"], m["avg_speed"], m["avg_travel_time"]), (0, 0.0, 0.0))

    def test_vehicles_complete_trips_on_valid_roads(self):
        e = Engine(seed=2)
        for _ in range(400):
            e.step()
            for (a, b), c in e.cells.items():
                for v in c.values(): self.assertEqual((v.route[v.i], v.route[v.i + 1]), (a, b))
        self.assertGreater(e.metrics()["completed"], 0)

    def test_red_light_blocks_and_queue_forms(self):
        e = Engine(rate=0); e.fixed_green = 10**6
        from sim.engine import Vehicle
        e.sig[1].green = "EW"        # node 1: NS road 4->1 is red
        for p in range(3): e.cells[(4, 1)][L - 1 - p] = Vehicle(p + 1, [4, 1, 0], 0, pos=L - 1 - p, i=0)
        for _ in range(20): e.step()
        self.assertEqual(sorted(e.cells[(4, 1)]), [L - 3, L - 2, L - 1]); self.assertEqual(e.queues()[1], 3)

    def test_no_conflicting_greens_and_no_overlap(self):
        e = Engine(scenario="high_congestion", strategy="adaptive")
        for _ in range(300): e.step()
        for c in e.cells.values(): self.assertEqual(len({v.id for v in c.values()}), len(c))
        self.assertTrue(all(s.green in ("NS", "EW") for s in e.sig.values()))   # one axis green per node

    def test_closure_blocks_entries_and_reroutes(self):
        e = Engine(rate=0); e.set_closed(3, 4, True)
        p = e.route(3, 5); self.assertIsNotNone(p); self.assertNotIn((3, 4), list(zip(p, p[1:])))
        e = Engine(rate=0.15)
        for _ in range(50): e.step()
        e.set_closed(3, 4, True)
        for _ in range(200): e.step()
        self.assertEqual(len(e.cells[(3, 4)]) + len(e.cells[(4, 3)]), 0)

    def test_unreachable(self):
        e = Engine(rate=0)
        for pair in ((0, 1), (0, 3)): e.set_closed(*pair, True)
        self.assertIsNone(e.route(2, 0))

    def test_determinism_and_strategies_run(self):
        r = []
        for s in ("fixed", "adaptive"):
            e = Engine(seed=5, strategy=s)
            for _ in range(200): e.step()
            r.append(e.metrics()["generated"])
        self.assertEqual(r[0], r[1])        # same seed -> same demand


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.app = create_app(os.path.join(self.d, "t.db")); self.c = self.app.test_client()

    def test_health_city_index(self):
        self.assertEqual(self.c.get("/api/health").json["engine"], "native")
        self.assertEqual(len(self.c.get("/api/city").json["roads"]), 24)
        self.assertEqual(self.c.get("/").status_code, 200)

    def test_pause_step_reset(self):
        self.c.post("/api/simulation/step"); self.assertEqual(self.c.get("/api/state").json["metrics"]["t"], 1)
        self.c.post("/api/simulation/reset"); self.assertEqual(self.c.get("/api/state").json["metrics"]["t"], 0)

    def test_no_duplicate_workers(self):
        self.c.post("/api/simulation/start"); w = self.app.S["worker"]
        self.c.post("/api/simulation/start"); self.assertIs(self.app.S["worker"], w)
        self.c.post("/api/simulation/pause")

    def test_invalid_requests(self):
        self.assertEqual(self.c.post("/api/roads/close", json={"a": 0, "b": 8}).status_code, 400)
        self.assertEqual(self.c.post("/api/config", json={"scenario": "nope"}).status_code, 400)
        self.assertEqual(self.c.post("/api/experiments/run", json={"steps": 1}).status_code, 400)
        self.assertEqual(self.c.post("/api/simulation/bogus").status_code, 404)

    def test_experiment_history_and_exports(self):
        r = self.c.post("/api/experiments/run", json={"steps": 200, "seed": 3}).json
        self.assertEqual(r["results"]["fixed"]["cfg"]["seed"], r["results"]["adaptive"]["cfg"]["seed"])
        self.assertEqual(r["results"]["fixed"]["metrics"]["generated"], r["results"]["adaptive"]["metrics"]["generated"])
        self.assertEqual(self.c.get("/api/experiments").json[0]["id"], r["id"])
        self.assertEqual(self.c.get(f"/api/experiments/{r['id']}").status_code, 200)
        self.assertIn("metric,fixed,adaptive", self.c.get("/api/exports/experiments.csv").text.replace("id,seed,", ""))
        for _ in range(60): self.c.post("/api/simulation/step")
        self.assertTrue(self.c.get("/api/exports/trips.csv").text.startswith("vehicle_id,spawn_t"))
        self.assertTrue(self.c.get("/api/exports/metrics.csv").text.startswith("t,active"))


if __name__ == "__main__":
    unittest.main()
