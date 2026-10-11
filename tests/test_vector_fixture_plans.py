"""Complex regression inputs are typed plans, never pre-generated production SVG."""

import json
from pathlib import Path

import pytest

from dcc_mcp_inkscape.plan import validate_plan


@pytest.mark.parametrize("name, expected", [("night-atlas-map", 188), ("night-atlas-fold", 83)])
def test_night_atlas_regression_plan(name, expected):
    path = Path(__file__).parent / "fixtures" / "night_atlas" / (name + ".json")
    payload = json.loads(path.read_text())
    plan = validate_plan(payload["plan"])
    assert len(plan["nodes"]) == expected
    assert len({node["id"] for node in plan["nodes"]}) == expected
    assert payload["output_file"] == "artwork/" + name + ".svg"
    assert sum(node["type"] == "layer" for node in plan["nodes"]) == 5
