# Mini Digital Twin City (native engine)

Simulated 3x3 signalised grid city (9 intersections, 12 two-way streets). Not connected to real traffic systems.

## Windows setup
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000 . Tests: `python -m unittest tests.test_all` (or `pytest`).

## What is implemented
- Native engine (1 s steps, 1 cell/step, 8 cells/road): vehicles follow Dijkstra routes, stop at red/yellow, queue, complete trips.
- Signals: fixed-time (configurable green) and adaptive (queue + 0.1*wait pressure, min 5 s / max 25 s green, 2 s all-red, one axis green at a time). Rule-based, not ML; can oscillate under symmetric load.
- Road closures (click a road in the map): block entries, reroute, remove vehicles whose destination becomes unreachable (counted as `stranded`).
- Metrics. Current: active, stopped, avg_speed, queues. Cumulative: generated, completed, total_wait, max_queue, avg wait and travel time of completed trips.
- Experiment Lab: both strategies with the same seed and demand, absolute and % change (n/a when baseline is 0), saved to SQLite; CSV/JSON exports.

## Limitations
- SUMO/TraCI is NOT implemented; the UI always shows "native". Only cars/buses (no emergency vehicles, pedestrians, RL).
- Charts are simple canvas charts (Plotly not bundled; no internet was available to fetch it).
- No browser-automation tests; the frontend was not exercised in a real browser.
- Tests: 12 unittest cases pass; pytest was not installable in the build environment.
