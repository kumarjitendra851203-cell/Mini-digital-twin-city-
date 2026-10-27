"""Native Python traffic engine (discrete 1-second steps, 3x3 signalised grid)."""
import heapq, random
from dataclasses import dataclass

N = 3          # 3x3 grid -> 9 intersections, 12 two-way streets (24 directed roads)
L = 8          # cells per road; one vehicle per cell, max speed 1 cell/step
SCENARIOS = {"normal": 0.04, "rush_hour": 0.10, "high_congestion": 0.20}  # spawn prob/step/entry
BORDER = [n for n in range(N * N) if n // N in (0, N - 1) or n % N in (0, N - 1)]


def _orient(a, b):
    return "NS" if abs(a - b) == N else "EW"


def build_edges():
    e = set()
    for n in range(N * N):
        r, c = divmod(n, N)
        for dr, dc in ((0, 1), (1, 0)):
            if r + dr < N and c + dc < N:
                m = (r + dr) * N + c + dc
                e |= {(n, m), (m, n)}
    return sorted(e)


@dataclass
class Vehicle:
    id: int
    route: list
    spawn: int
    kind: str = "car"
    i: int = 0          # index (in route) of current edge's start node
    pos: int = 0
    wait: int = 0
    moved: bool = True


@dataclass
class Signal:
    green: str = "NS"
    t: int = 0
    yellow: int = 0
    nxt: str = ""
    switches: int = 0


class Engine:
    name = "native"

    def __init__(self, seed=1, scenario="normal", strategy="fixed", fixed_green=10,
                 min_green=5, max_green=25, rate=None):
        if scenario not in SCENARIOS or strategy not in ("fixed", "adaptive"):
            raise ValueError("bad scenario or strategy")
        self.seed, self.scenario, self.strategy = seed, scenario, strategy
        self.fixed_green, self.min_green, self.max_green = fixed_green, min_green, max_green
        self.rate = SCENARIOS[scenario] if rate is None else rate
        self.rng = random.Random(seed)
        self.edges = build_edges()
        self.closed = set()
        self.closure_log = []
        self.cells = {e: {} for e in self.edges}          # edge -> {pos: Vehicle}
        self.sig = {n: Signal("NS" if n % 2 == 0 else "EW") for n in range(N * N)}
        self.t = self.vid = 0
        self.generated = self.blocked_spawns = self.stranded = 0
        self.done = []                                    # (id, spawn, end, wait, hops)
        self.total_wait = self.moved_sum = self.max_queue = self._tp = 0
        self.series = []

    # ---- routing: Dijkstra over open roads ----
    def route(self, a, b):
        dist, prev, h = {a: 0}, {}, [(0, a)]
        while h:
            d, u = heapq.heappop(h)
            if u == b:
                p = [b]
                while p[-1] != a:
                    p.append(prev[p[-1]])
                return p[::-1]
            if d > dist[u]:
                continue
            for (x, y) in self.edges:
                if x == u and (x, y) not in self.closed and d + 1 < dist.get(y, 1e9):
                    dist[y] = d + 1; prev[y] = u; heapq.heappush(h, (d + 1, y))
        return None

    def set_closed(self, a, b, closed):
        if (a, b) not in self.cells:
            raise ValueError("unknown road")
        pair = {(a, b), (b, a)}
        self.closed = (self.closed | pair) if closed else (self.closed - pair)
        self.closure_log.append({"t": self.t, "road": f"{a}-{b}", "closed": closed})
        rerouted = removed = 0
        for (x, y), c in self.cells.items():
            for pos, v in list(c.items()):
                cur = y                      # vehicles finish their current road, then re-plan
                np = [cur] if cur == v.route[-1] else self.route(cur, v.route[-1])
                if np is None:               # unreachable: remove and report honestly
                    del c[pos]; self.stranded += 1; removed += 1
                elif v.route[v.i + 1:] != np:
                    v.route = v.route[:v.i + 1] + np; rerouted += 1
        return {"rerouted": rerouted, "removed_unreachable": removed}

    def vehicles(self):
        return [v for c in self.cells.values() for v in c.values()]

    # ---- signals: fixed-time or queue/wait-aware adaptive with 2 s all-red ----
    def _pressure(self, n, o):
        p = 0.0
        for (x, y), c in self.cells.items():
            if y == n and _orient(x, y) == o:
                p += sum(1 + 0.1 * v.wait for v in c.values() if v.pos >= L - 4 and not v.moved)
        return p

    def _signals(self):
        for n, s in self.sig.items():
            if s.yellow:
                s.yellow -= 1
                if not s.yellow:
                    s.green, s.t = s.nxt, 0
                continue
            s.t += 1
            other = "EW" if s.green == "NS" else "NS"
            if self.strategy == "fixed":
                sw = s.t >= self.fixed_green
            else:
                pc, po = self._pressure(n, s.green), self._pressure(n, other)
                sw = s.t >= self.min_green and (po > pc * 1.2 or (pc == 0 and po > 0) or
                                                (s.t >= self.max_green and po > 0))
            if sw:
                s.yellow, s.nxt, s.switches = 2, other, s.switches + 1

    # ---- one simulation step ----
    def step(self):
        self._signals()
        tp = 0
        for e in self.edges:
            c = self.cells[e]
            for pos in sorted(c, reverse=True):                 # front vehicle first
                v = c.get(pos)
                if v is None:
                    continue
                v.moved = False
                if pos < L - 1:
                    if pos + 1 not in c:
                        del c[pos]; v.pos += 1; c[v.pos] = v; v.moved = True
                else:
                    node = e[1]
                    s = self.sig[node]
                    if node == v.route[-1]:                     # arrived
                        del c[pos]; tp += 1
                        self.done.append((v.id, v.spawn, self.t, v.wait, len(v.route) - 1))
                        continue
                    ne = (node, v.route[v.i + 2])
                    if ne in self.closed:                       # re-plan at the junction
                        np = self.route(node, v.route[-1])
                        if np is None:
                            del c[pos]; self.stranded += 1; continue
                        v.route = v.route[:v.i + 1] + np; ne = (node, v.route[v.i + 2])
                    if not s.yellow and s.green == _orient(*e) and 0 not in self.cells[ne]:
                        del c[pos]; v.i += 1; v.pos = 0; self.cells[ne][0] = v; v.moved = True
                if not v.moved:
                    v.wait += 1
        self._spawn()
        self.t += 1
        vs = self.vehicles()
        self.total_wait += sum(1 for v in vs if not v.moved)
        self.max_queue = max([self.max_queue] + list(self.queues().values()))
        self._tp += tp
        if self.t % 5 == 0:
            m = self.metrics(); m["throughput_interval"] = self._tp; self._tp = 0
            self.series.append(m)

    def _spawn(self):
        for o in BORDER:
            if self.rng.random() < self.rate:
                d = self.rng.choice([x for x in BORDER if x != o])
                kind = self.rng.choice(["car"] * 8 + ["bus"] * 2)
                p = self.route(o, d)
                self.generated += 1
                if p is None:
                    self.stranded += 1; continue
                e = (o, p[1])
                if 0 in self.cells[e]:
                    self.blocked_spawns += 1; continue
                self.vid += 1
                self.cells[e][0] = Vehicle(self.vid, p, self.t, kind)

    # ---- metrics (current vs cumulative documented in README) ----
    def queues(self):
        q = {n: 0 for n in range(N * N)}
        for (x, y), c in self.cells.items():
            q[y] += sum(1 for v in c.values() if not v.moved and v.pos >= L - 4)
        return q

    def metrics(self):
        vs = self.vehicles(); n = len(vs); d = len(self.done)
        return {"t": self.t, "active": n, "generated": self.generated, "completed": d,
                "avg_wait_completed": sum(x[3] for x in self.done) / d if d else 0.0,
                "avg_travel_time": sum(x[2] - x[1] for x in self.done) / d if d else 0.0,
                "avg_speed": sum(1 for v in vs if v.moved) / n if n else 0.0,
                "total_wait": self.total_wait, "max_queue": self.max_queue,
                "stopped": sum(1 for v in vs if not v.moved), "stranded": self.stranded,
                "blocked_spawns": self.blocked_spawns, "queues": self.queues(),
                "signal_switches": sum(s.switches for s in self.sig.values())}

    def snapshot(self):
        veh = [{"id": v.id, "e": [a, b], "p": v.pos / L, "k": v.kind, "s": int(not v.moved)}
               for (a, b), c in self.cells.items() for v in c.values()]
        load = {f"{a}-{b}": (-1 if (a, b) in self.closed else len(c) / L) for (a, b), c in self.cells.items()}
        return {"t": self.t, "engine": self.name, "strategy": self.strategy, "scenario": self.scenario,
                "vehicles": veh, "load": load, "closed": sorted(f"{a}-{b}" for a, b in self.closed),
                "signals": {n: ("Y" if s.yellow else s.green) for n, s in self.sig.items()}}
