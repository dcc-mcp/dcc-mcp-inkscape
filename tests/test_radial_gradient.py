"""Bounded radial-gradient regressions; ordinary tests never launch a DCC.

SVG/XML emitted here is synthetic acceptance data, never production artwork.
The real native smoke additionally requires DCC_MCP_INKSCAPE_RADIAL_LIVE_TEST=1.
"""

from __future__ import annotations

import copy
import json
import os
import struct
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree

import pytest
from dcc_mcp_core import ToolValidator

from dcc_mcp_inkscape import plan as plan_module
from dcc_mcp_inkscape import runtime as runtime_module

SVG = "{http://www.w3.org/2000/svg}"
EXAMPLE = Path(runtime_module.__file__).resolve().parent


@pytest.fixture
def radial_plan():
    return {
        "canvas": {"width": 64, "height": 64},
        "nodes": [
            {
                "type": "radial_gradient",
                "id": "glow",
                "cx": 32,
                "cy": 32,
                "r": 24,
                "stops": [
                    {"offset": 0, "color": "#ff0000", "opacity": 1},
                    {"offset": 1, "color": "#0000ff", "opacity": 0},
                ],
            },
            {
                "type": "path",
                "id": "shape",
                "d": "M8,32 C8,4 56,4 56,32 C56,60 8,60 8,32 Z",
                "fill": "url(#glow)",
            },
        ],
    }


@pytest.fixture
def runtime(tmp_path):
    executable = tmp_path / "bin" / "inkscape.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"Synthetic executable fixture: never executed")
    return runtime_module.InkscapeRuntime(executable, tmp_path / "workspace")


def _public_validation(plan):
    descriptor = json.loads((EXAMPLE / "skills/inkscape-vector/tools.yaml").read_text(encoding="utf-8"))
    tool = next(item for item in descriptor["tools"] if item["name"] == "document_build")
    validator = ToolValidator.from_schema_json(json.dumps(tool["input_schema"]))
    return validator.validate(json.dumps({"output_file": "radial.svg", "plan": plan}))


@pytest.mark.parametrize("kind", ["radial_gradient", "radialGradient"])
@pytest.mark.parametrize("units", ["userSpaceOnUse", "objectBoundingBox"])
def test_radial_alias_units_and_independent_focal_defaults_preserve_caller_data(radial_plan, kind, units):
    node = radial_plan["nodes"][0]
    node.update(type=kind, gradient_units=units, cx=-0.25, cy=1.25, r=1.5, fx=0.75)
    before = copy.deepcopy(radial_plan)
    normalized = plan_module.validate_plan(radial_plan)
    actual = normalized["nodes"][0]
    assert actual["type"] == "radial_gradient"
    assert actual["fx"] == 0.75
    assert actual["fy"] == 1.25
    assert radial_plan == before
    ok, errors = _public_validation(radial_plan)
    assert ok, errors


@pytest.mark.parametrize("radius", [1e-9, 0.5, 1.5, 1000000])
@pytest.mark.parametrize("units", ["userSpaceOnUse", "objectBoundingBox"])
def test_radial_radius_bounds_do_not_restrict_bbox_to_a_unit_circle(radial_plan, radius, units):
    radial_plan["nodes"][0].update(r=radius, gradient_units=units)
    assert plan_module.validate_plan(radial_plan)["nodes"][0]["r"] == radius
    ok, errors = _public_validation(radial_plan)
    assert ok, errors


@pytest.mark.parametrize("field", ["cx", "cy", "fx", "fy"])
@pytest.mark.parametrize("value", [-1000000, 1000000])
def test_radial_coordinate_bounds_agree_with_the_public_schema(radial_plan, field, value):
    radial_plan["nodes"][0][field] = value
    assert plan_module.validate_plan(radial_plan)["nodes"][0][field] == value
    ok, errors = _public_validation(radial_plan)
    assert ok, errors


@pytest.mark.parametrize("field", ["cx", "cy", "r", "stops"])
def test_radial_required_fields_are_enforced_by_public_and_native_validation(radial_plan, field):
    radial_plan["nodes"][0].pop(field)
    ok, errors = _public_validation(radial_plan)
    assert not ok
    assert errors
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize("field", ["cx", "cy", "r", "fx", "fy"])
@pytest.mark.parametrize("value", [True, None, "50%", {}, [], 1000001])
def test_public_radial_schema_rejects_malformed_numeric_fields(radial_plan, field, value):
    radial_plan["nodes"][0][field] = value
    ok, errors = _public_validation(radial_plan)
    assert not ok
    assert errors
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize("field", ["cx", "cy", "r", "fx", "fy"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -1000001])
def test_native_radial_coordinates_cannot_escape_finite_bounds(radial_plan, field, value):
    radial_plan["nodes"][0][field] = value
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize("radius", [-1, 0, 1e-10])
def test_subminimum_radial_radius_is_rejected_at_both_boundaries(radial_plan, radius):
    radial_plan["nodes"][0]["r"] = radius
    ok, errors = _public_validation(radial_plan)
    assert not ok
    assert errors
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize("field", ["fx", "fy"])
@pytest.mark.parametrize("kind", ["path", "circle", "linear_gradient", "linearGradient"])
def test_focal_coordinates_are_not_silently_accepted_on_another_object_kind(radial_plan, field, kind):
    node = radial_plan["nodes"][0]
    node.update(type=kind, d="M0,0 L1,1")
    node[field] = 0.5
    with pytest.raises(ValueError, match="Focal coordinates"):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize("reference", ["glow", "rim"])
def test_paint_references_support_both_native_gradient_kinds(radial_plan, reference):
    linear = {"type": "linearGradient", "id": "rim", "stops": copy.deepcopy(radial_plan["nodes"][0]["stops"])}
    radial_plan["nodes"].insert(1, linear)
    radial_plan["nodes"][-1].update(fill="url(#" + reference + ")", stroke="url(#" + reference + ")")
    normalized = plan_module.validate_plan(radial_plan)
    assert normalized["nodes"][1]["type"] == "linear_gradient"


@pytest.mark.parametrize("reference", ["missing", "shape"])
def test_missing_or_non_gradient_paint_references_are_rejected(radial_plan, reference):
    radial_plan["nodes"][-1]["fill"] = "url(#" + reference + ")"
    with pytest.raises(ValueError, match="Unknown gradient reference"):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize("count", [0, 1, 65])
def test_radial_stop_count_is_bounded_in_public_and_native_contracts(radial_plan, count):
    radial_plan["nodes"][0]["stops"] = [
        {"offset": index / max(1, count - 1), "color": "#1696d2"} for index in range(count)
    ]
    ok, errors = _public_validation(radial_plan)
    assert not ok
    assert errors
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [("offset", -0.1), ("offset", 1.1), ("opacity", False), ("opacity", 1.1), ("color", "url(#shape)")],
)
def test_radial_stops_remain_ordered_literal_bounded_paints(radial_plan, field, value):
    radial_plan["nodes"][0]["stops"][0][field] = value
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)


def test_radial_stop_order_and_stop_field_allowlist_are_enforced(radial_plan):
    radial_plan["nodes"][0]["stops"].reverse()
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)
    radial_plan["nodes"][0]["stops"].reverse()
    radial_plan["nodes"][0]["stops"][0]["onload"] = "run()"
    with pytest.raises(ValueError):
        plan_module.validate_plan(radial_plan)


@pytest.mark.parametrize("failure", ["parent", "unsafe-id", "duplicate-id", "transform", "geometry", "radius"])
def test_invalid_radial_plan_is_rejected_before_a_native_host_starts(runtime, monkeypatch, radial_plan, failure):
    node = radial_plan["nodes"][0]
    if failure == "parent":
        radial_plan["nodes"].insert(0, {"type": "group", "id": "container"})
        node["parent"] = "container"
    elif failure == "unsafe-id":
        node["id"] = "glow;export-do"
    elif failure == "duplicate-id":
        node["id"] = "shape"
    elif failure == "transform":
        node["gradient_transform"] = "translate(1,2);export-do"
    elif failure == "geometry":
        node["x1"] = 0
    else:
        node["r"] = 1e-10
    run = Mock(side_effect=AssertionError("Invalid plans must not start native software"))
    monkeypatch.setattr(runtime, "_run", run)
    with pytest.raises(ValueError):
        runtime.document_build("rejected-radial.svg", radial_plan)
    run.assert_not_called()
    assert not (runtime.workspace / "rejected-radial.svg").exists()


@pytest.fixture
def saved_tree():
    return ElementTree.ElementTree(
        ElementTree.fromstring(
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
            '<defs><radialGradient id="glow" cx="32" cy="32" r="24" fx="32" fy="32" '
            'gradientUnits="userSpaceOnUse">'
            '<stop offset="0" style="stop-color:#ff0000;stop-opacity:1"/>'
            '<stop offset="1" style="stop-color:#0000ff;stop-opacity:0"/>'
            '</radialGradient></defs><path id="shape" fill="url(#glow)" '
            'd="M8,32 C8,4 56,4 56,32 C56,60 8,60 8,32 Z"/></svg>'
        )
    )


def _save_fixture(directory, tree):
    source = directory / "synthetic-radial.svg"
    tree.write(source, encoding="utf-8", xml_declaration=True)
    return source


def _damage_definition(tree, failure):
    root = tree.getroot()
    defs = root.find(SVG + "defs")
    gradient = defs.find(SVG + "radialGradient")
    if failure == "root-placement":
        defs.remove(gradient)
        root.append(gradient)
    elif failure == "nested-defs":
        root.remove(defs)
        group = ElementTree.SubElement(root, SVG + "g")
        group.append(defs)
    elif failure == "foreign-defs":
        defs.tag = "{urn:foreign}defs"
    elif failure == "foreign-gradient":
        gradient.tag = "{urn:foreign}radialGradient"
    elif failure == "linear-tag":
        gradient.tag = SVG + "linearGradient"
    elif failure == "missing-id":
        gradient.attrib.pop("id")
    elif failure == "duplicate-id":
        defs.append(copy.deepcopy(gradient))
    elif failure == "units":
        gradient.set("gradientUnits", "objectBoundingBox")
    elif failure == "transform":
        gradient.set("gradientTransform", "matrix(1,0,0,1,10,20)")
    elif failure == "empty-stops":
        for stop in list(gradient):
            gradient.remove(stop)
    elif failure == "foreign-stop":
        gradient[0].tag = "{urn:foreign}stop"
    elif failure == "offset":
        gradient[0].set("offset", "0.2")
    elif failure == "opacity":
        gradient[0].set("style", "stop-color:#ff0000;stop-opacity:0.25")
    elif failure == "missing-color":
        gradient[0].set("style", "stop-opacity:1")
    elif failure == "color":
        gradient[0].set("style", "stop-color:#00ff00;stop-opacity:1")
    elif failure.startswith("missing-"):
        gradient.attrib.pop(failure[len("missing-") :])
    else:
        field, value = failure.split("=", 1)
        gradient.set(field, value)


def test_saved_radial_definition_preserves_native_ids_geometry_units_and_stops(tmp_path, saved_tree, radial_plan):
    source = _save_fixture(tmp_path, saved_tree)
    tree = runtime_module.verify_document(source, plan_module.validate_plan(radial_plan))
    assert tree.getroot().find(SVG + "defs/" + SVG + "radialGradient").get("id") == "glow"


@pytest.mark.parametrize("requested", ["#f00", "#FF0000"])
@pytest.mark.parametrize("saved", ["#f00", "#ff0000", "rgb(255,0,0)", "RGB( 255, 0, 0 )"])
def test_saved_opaque_stop_color_accepts_equivalent_native_rgb_serialization(
    tmp_path, saved_tree, radial_plan, requested, saved
):
    radial_plan["nodes"][0]["stops"][0]["color"] = requested
    gradient = saved_tree.getroot().find(SVG + "defs/" + SVG + "radialGradient")
    gradient[0].set("style", "stop-color:" + saved + ";stop-opacity:1")
    runtime_module.verify_document(_save_fixture(tmp_path, saved_tree), plan_module.validate_plan(radial_plan))


@pytest.mark.parametrize("saved", ["#0f0", "#00ff00", "rgb(0,255,0)", "rgb(256,0,0)"])
def test_saved_opaque_red_stop_rejects_green_or_invalid_native_rgb(tmp_path, saved_tree, radial_plan, saved):
    gradient = saved_tree.getroot().find(SVG + "defs/" + SVG + "radialGradient")
    gradient[0].set("style", "stop-color:" + saved + ";stop-opacity:1")
    with pytest.raises(RuntimeError):
        runtime_module.verify_document(_save_fixture(tmp_path, saved_tree), plan_module.validate_plan(radial_plan))


@pytest.mark.parametrize("units", ["userSpaceOnUse", "objectBoundingBox"])
def test_saved_gradient_transform_accepts_affine_equivalence_not_literal_string_equality(
    tmp_path, saved_tree, radial_plan, units
):
    radial_plan["nodes"][0].update(gradient_units=units, gradient_transform="translate(10,20) scale(2,3)")
    gradient = saved_tree.getroot().find(SVG + "defs/" + SVG + "radialGradient")
    gradient.set("gradientUnits", units)
    gradient.set("gradientTransform", "matrix(2,0,0,3,10,20)")
    runtime_module.verify_document(_save_fixture(tmp_path, saved_tree), plan_module.validate_plan(radial_plan))
    gradient.set("gradientTransform", "matrix(2,0,0,3,20,60)")
    with pytest.raises(RuntimeError):
        runtime_module.verify_document(_save_fixture(tmp_path, saved_tree), plan_module.validate_plan(radial_plan))


@pytest.mark.parametrize(
    "failure",
    [
        "root-placement",
        "nested-defs",
        "foreign-defs",
        "foreign-gradient",
        "linear-tag",
        "missing-id",
        "duplicate-id",
        "units",
        "transform",
        "empty-stops",
        "foreign-stop",
        "offset",
        "opacity",
        "missing-color",
        "missing-cx",
        "missing-cy",
        "missing-r",
        "missing-fx",
        "missing-fy",
        "cx=32.1",
        "cy=nan",
        "r=inf",
        "fx=32.1",
        "fy=not-a-number",
    ],
)
def test_saved_radial_defects_are_rejected_before_publication(tmp_path, saved_tree, radial_plan, failure):
    _damage_definition(saved_tree, failure)
    with pytest.raises(RuntimeError):
        runtime_module.verify_document(_save_fixture(tmp_path, saved_tree), plan_module.validate_plan(radial_plan))


def test_tolerance_cannot_turn_the_minimum_positive_saved_radius_into_zero(tmp_path, saved_tree, radial_plan):
    radial_plan["nodes"][0]["r"] = 1e-9
    saved_tree.getroot().find(SVG + "defs/" + SVG + "radialGradient").set("r", "0")
    with pytest.raises(RuntimeError):
        runtime_module.verify_document(_save_fixture(tmp_path, saved_tree), plan_module.validate_plan(radial_plan))


@pytest.mark.parametrize("failure", ["root-placement", "units", "transform", "empty-stops", "color"])
def test_native_radial_acceptance_failure_never_commits_an_output(
    runtime, monkeypatch, radial_plan, saved_tree, failure
):
    _damage_definition(saved_tree, failure)

    def simulated_host(arguments, environment=None, timeout=120, host_report=None, native_provenance=False):
        request_path = Path(environment["DCC_MCP_INKSCAPE_REQUEST"])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        evidence = {
            "nonce": request["nonce"],
            "self_call": "true",
            "parent_pid": 4321,
            "extension_pid": 4322,
            "parent_executable": str(runtime.executable),
            "object_count": len(radial_plan["nodes"]),
        }
        Path(request["evidence_path"]).write_text(json.dumps(evidence), encoding="utf-8")
        saved_tree.write(request_path.parent / "result.svg", encoding="utf-8", xml_declaration=True)
        return {"host_pid": 4321}

    monkeypatch.setattr(runtime, "_run", simulated_host)
    commit = Mock(side_effect=AssertionError("Unverified radial definitions must not be published"))
    monkeypatch.setattr(runtime, "_commit", commit)
    with pytest.raises(RuntimeError):
        runtime.document_build("unverified-radial.svg", radial_plan)
    commit.assert_not_called()
    assert not (runtime.workspace / "unverified-radial.svg").exists()


@pytest.mark.dcc
@pytest.mark.parametrize("units", ["userSpaceOnUse", "objectBoundingBox"])
@pytest.mark.skipif(
    os.environ.get("DCC_MCP_INKSCAPE_RADIAL_LIVE_TEST") != "1" or not os.environ.get("DCC_MCP_INKSCAPE_EXE"),
    reason="Radial native smoke requires explicit radial opt-in and a configured executable",
)
def test_opted_in_native_radial_bezier_build_inspect_and_rgba_export(tmp_path, radial_plan, units):
    gradient = radial_plan["nodes"][0]
    if units == "userSpaceOnUse":
        gradient.update(gradient_units=units, fx=26, fy=30, gradient_transform="translate(0,3) scale(1,0.85)")
    else:
        gradient.update(
            gradient_units=units,
            cx=0.5,
            cy=0.5,
            r=0.7,
            fx=0.45,
            fy=0.45,
            gradient_transform="rotate(10,.5,.5) scale(1,1.2)",
        )
    runtime = runtime_module.InkscapeRuntime(
        os.environ["DCC_MCP_INKSCAPE_EXE"], tmp_path / "isolated-radial-native-workspace"
    )
    built = runtime.document_build("radial-bezier.svg", radial_plan)
    assert built["native_effect"]["self_call"] == "true"
    assert built["native_effect"]["object_count"] == 2
    tree = runtime_module.verify_document(Path(built["output_path"]), plan_module.validate_plan(radial_plan))
    assert len(tree.findall(".//" + SVG + "radialGradient")) == 1
    inspected = runtime.document_inspect("radial-bezier.svg")
    assert inspected["element_counts"]["radialGradient"] == 1
    assert inspected["element_counts"]["path"] == 1
    assert "shape," in inspected["geometry"]
    exported = runtime.document_export(
        "radial-bezier.svg", "radial-bezier-64.png", width=64, height=64, background_opacity=0
    )
    png = Path(exported["output_path"]).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (64, 64)
    assert png[24:26] == bytes((8, 6)), "Colored transparent gradient must export a real 8-bit RGBA PNG"
