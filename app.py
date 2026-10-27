"""Flask API for Mini Digital Twin City. Engine in sim/engine.py (native; SUMO not bundled)."""
import io, json, sqlite3, threading, time
import pandas as pd
from flask import Flask, jsonify, request, render_template, Response
from sim.engine import Engine, SCENARIOS, N


def create_app(db="twin.db"):
    app = Flask(__name__)
    lock = threading.RLock()
    S = {"eng": Engine(), "running": False, "worker": None, "speed": 5, "cfg": {}}

    def con():
        c = sqlite3.connect(db); c.execute("CREATE TABLE IF NOT EXISTS exp(id INTEGER PRIMARY KEY, created REAL, data TEXT)")
        return c

    def err(msg, code=400):
        return jsonify(error=msg), code

    def loop():
        while True:
            with lock:
                if not S["running"]:
                    S["worker"] = None; return
                S["eng"].step(); sp = S["speed"]
            time.sleep(1.0 / sp)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", engine="native", sumo_available=False)

    @app.get("/api/city")
    def city():
        return jsonify(grid=N, roads=[f"{a}-{b}" for a, b in S["eng"].edges], scenarios=SCENARIOS)

    @app.get("/api/state")
    def state():
        with lock:
            return jsonify(running=S["running"], speed=S["speed"], snapshot=S["eng"].snapshot(),
                           metrics=S["eng"].metrics(), series=S["eng"].series[-120:])

    @app.get("/api/metrics")
    def metrics():
        with lock:
            return jsonify(S["eng"].metrics())

    @app.post("/api/simulation/<act>")
    def sim(act):
        with lock:
            if act == "start" or act == "resume":
                S["running"] = True
                if S["worker"] is None:                      # never spawn a duplicate worker
                    S["worker"] = threading.Thread(target=loop, daemon=True); S["worker"].start()
            elif act == "pause":
                S["running"] = False
            elif act == "step":
                if S["running"]:
                    return err("pause first", 409)
                S["eng"].step()
            elif act == "reset":
                S["running"] = False
                S["eng"] = Engine(**S["cfg"])
            else:
                return err("unknown action", 404)
            return jsonify(running=S["running"], t=S["eng"].t)

    @app.post("/api/config")
    def config():
        j = request.get_json(silent=True) or {}
        cfg = {}
        try:
            if "scenario" in j: cfg["scenario"] = str(j["scenario"])
            if "strategy" in j: cfg["strategy"] = str(j["strategy"])
            if "seed" in j: cfg["seed"] = int(j["seed"])
            if "fixed_green" in j:
                cfg["fixed_green"] = int(j["fixed_green"])
                if not 3 <= cfg["fixed_green"] <= 60: return err("fixed_green must be 3-60")
            if "rate" in j:
                cfg["rate"] = float(j["rate"])
                if not 0 <= cfg["rate"] <= 0.5: return err("rate must be 0-0.5")
            if "speed" in j:
                if not 1 <= int(j["speed"]) <= 50: return err("speed must be 1-50")
                S["speed"] = int(j["speed"])
            if cfg:
                with lock:
                    new = {**S["cfg"], **cfg}; Engine(**new)      # validates
                    S["cfg"] = new; S["running"] = False; S["eng"] = Engine(**new)
        except (ValueError, TypeError) as e:
            return err(str(e))
        return jsonify(ok=True, cfg=S["cfg"])

    @app.post("/api/roads/<act>")
    def road(act):
        j = request.get_json(silent=True) or {}
        try:
            a, b = int(j["a"]), int(j["b"])
            with lock:
                if act not in ("close", "open"): return err("unknown action", 404)
                return jsonify(S["eng"].set_closed(a, b, act == "close"))
        except (KeyError, ValueError, TypeError) as e:
            return err(f"invalid road: {e}")

    def run_one(cfg, steps, close):
        e = Engine(**cfg); info = None
        for i in range(steps):
            if close and i == steps // 4: info = e.set_closed(*close, True)
            e.step()
        m = e.metrics(); m.pop("queues")
        return {"cfg": cfg, "metrics": m, "closure": info, "series": e.series}

    @app.post("/api/experiments/run")
    def run_exp():
        j = request.get_json(silent=True) or {}
        try:
            steps = int(j.get("steps", 600)); seed = int(j.get("seed", 1))
            if not 50 <= steps <= 5000: return err("steps must be 50-5000")
            base = {"seed": seed, "scenario": j.get("scenario", "rush_hour")}
            if "rate" in j: base["rate"] = float(j["rate"])
            close = (int(j["close"][0]), int(j["close"][1])) if j.get("close") else None
            res = {s: run_one({**base, "strategy": s}, steps, close) for s in ("fixed", "adaptive")}
        except (ValueError, TypeError, KeyError) as e:
            return err(str(e))
        cmp = {}
        for k in ("avg_wait_completed", "completed", "avg_travel_time"):
            f, a = res["fixed"]["metrics"][k], res["adaptive"]["metrics"][k]
            cmp[k] = {"fixed": f, "adaptive": a, "abs_change": a - f,
                      "pct_change": (a - f) / f * 100 if f else None}
        w = cmp["avg_wait_completed"]; better = "adaptive" if w["abs_change"] < 0 else "fixed" if w["abs_change"] > 0 else "neither"
        out = {"seed": seed, "steps": steps, "scenario": base["scenario"], "engine": "native", "closure": close,
               "results": res, "comparison": cmp,
               "report": f"Seed {seed}, {steps}s, {base['scenario']}: lower average wait for {better}. "
                         f"Throughput fixed={cmp['completed']['fixed']} vs adaptive={cmp['completed']['adaptive']}. "
                         "Result is for this seed only; adaptive control is not guaranteed to win."}
        c = con(); cur = c.execute("INSERT INTO exp(created,data) VALUES(?,?)", (time.time(), json.dumps(out)))
        out["id"] = cur.lastrowid; c.commit(); c.close()
        return jsonify(out)

    @app.get("/api/experiments")
    def exps():
        c = con(); rows = c.execute("SELECT id,created,data FROM exp ORDER BY id DESC").fetchall(); c.close()
        return jsonify([{"id": i, "created": t, "scenario": json.loads(d)["scenario"], "seed": json.loads(d)["seed"],
                         "report": json.loads(d)["report"]} for i, t, d in rows])

    @app.get("/api/experiments/<int:i>")
    def exp_get(i):
        c = con(); r = c.execute("SELECT data FROM exp WHERE id=?", (i,)).fetchone(); c.close()
        return jsonify(json.loads(r[0])) if r else err("not found", 404)

    def csv(df, name):
        return Response(df.to_csv(index=False), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.get("/api/exports/trips.csv")
    def trips():
        with lock:
            df = pd.DataFrame(S["eng"].done, columns=["vehicle_id", "spawn_t", "end_t", "wait_s", "hops"])
        df["travel_s"] = df.end_t - df.spawn_t
        return csv(df, "trips.csv")

    @app.get("/api/exports/metrics.csv")
    def metrics_csv():
        with lock:
            rows = [{k: v for k, v in m.items() if k != "queues"} for m in S["eng"].series]
        return csv(pd.DataFrame(rows, columns=["t", "active", "generated", "completed", "avg_wait_completed",
                   "avg_travel_time", "avg_speed", "total_wait", "max_queue", "stopped", "stranded",
                   "blocked_spawns", "signal_switches", "throughput_interval"]), "metrics.csv")

    @app.get("/api/exports/experiments.json")
    def exp_json():
        c = con(); rows = [json.loads(d) | {"id": i} for i, d in c.execute("SELECT id,data FROM exp")]; c.close()
        return Response(json.dumps(rows, indent=1), mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=experiments.json"})

    @app.get("/api/exports/experiments.csv")
    def exp_csv():
        c = con(); rows = []
        for i, d in c.execute("SELECT id,data FROM exp"):
            d = json.loads(d)
            for k, v in d["comparison"].items(): rows.append({"id": i, "seed": d["seed"], "metric": k, **v})
        c.close()
        return csv(pd.DataFrame(rows, columns=["id", "seed", "metric", "fixed", "adaptive", "abs_change", "pct_change"]), "experiments.csv")

    app.S = S
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
