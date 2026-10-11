"""Complex regression inputs are typed plans, never pre-generated production SVG."""

import json
from pathlib import Path

import pytest

from dcc_mcp_inkscape.plan import validate_plan


@pytest.mark.parametrize(
    "name, expected", [("night-atlas-map", 189), ("night-atlas-fold", 83), ("night-atlas-signs", 45)]
)
def test_night_atlas_regression_plan(name, expected):
    path = Path(__file__).parent / "fixtures" / "night_atlas" / (name + ".json")
    payload = json.loads(path.read_text())
    plan = validate_plan(payload["plan"])
    assert len(plan["nodes"]) == expected
    assert len({node["id"] for node in plan["nodes"]}) == expected
    assert payload["output_file"] == "artwork/" + name + ".svg"
    assert sum(node["type"] == "layer" for node in plan["nodes"]) == 5


def test_lantern_loop_visits_every_numbered_stop_and_returns_to_start():
    import re

    path = Path(__file__).parent / "fixtures" / "night_atlas" / "night-atlas-map.json"
    nodes = json.loads(path.read_text())["plan"]["nodes"]
    route = next(node["d"] for node in nodes if node["id"] == "route-a")
    tokens = iter(re.findall(r"[MLHV]|-?[0-9]+(?:\.[0-9]+)?", route))
    x = y = 0
    points = []
    for command in tokens:
        if command in {"M", "L"}:
            x, y = float(next(tokens)), float(next(tokens))
        elif command == "H":
            x = float(next(tokens))
        elif command == "V":
            y = float(next(tokens))
        else:
            raise AssertionError("Unexpected route geometry")
        points.append((x, y))
    assert points[0] == points[-1]
    stops = [node for node in nodes if re.fullmatch(r"place-[0-9]{2}", node["id"])]
    assert len(stops) == 8
    for stop in stops:
        x, y = stop["cx"], stop["cy"]
        assert any(
            abs((x - ax) * (by - ay) - (y - ay) * (bx - ax)) < 1e-6
            and min(ax, bx) <= x <= max(ax, bx)
            and min(ay, by) <= y <= max(ay, by)
            for (ax, ay), (bx, by) in zip(points, points[1:])
        ), stop["id"]
