"""Run committed typed-vector plans through real CLI/gateway/Inkscape tools.

Raw evidence stays in the CI artifact. This is a regression render, not an
assertion that artwork has passed visual review or is ready for publication.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.output.resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    evidence = workspace / "evidence"
    evidence.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "DCC_MCP_REGISTRY_DIR": str(workspace / "registry"),
            "DCC_MCP_LOG_DIR": str(workspace / "logs"),
            "DCC_MCP_CHECKPOINT_DIR": str(workspace / "checkpoints"),
            "DCC_MCP_JOB_STORAGE_PATH": str(workspace / "jobs.sqlite"),
            "DCC_MCP_CLI_REQUIRE_GATEWAY": "true",
            "DCC_MCP_AGENT_SESSION_ID": "native-vector-regression",
        }
    )
    cli = shutil.which("dcc-mcp-cli")
    gateway = shutil.which("dcc-mcp-server")
    if not cli or not gateway:
        raise RuntimeError("Install the official pinned CLI and Core server first")
    records = []
    count = 0

    def run_cli(name, *arguments):
        nonlocal count
        count += 1
        command = [cli, "--require-gateway", "--agent-session-id", "native-vector-regression", *arguments]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=180)
        record = {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        (evidence / ("%02d-%s.json" % (count, name))).write_text(json.dumps(record, indent=2))
        if result.returncode:
            raise RuntimeError("%s failed: %s" % (name, result.stderr))
        return json.loads(result.stdout)

    def call(name, slug, payload):
        request = evidence / (name + "-request.json")
        request.write_text(json.dumps(payload, indent=2))
        result = run_cli(name, "call", slug, "--json-file", str(request))
        if result.get("control_route") != "gateway" or result.get("gateway_stats_recorded") is not True:
            raise RuntimeError("Call did not preserve the required gateway route")
        if result.get("output", {}).get("success") is not True:
            raise RuntimeError("Native tool returned a failure envelope")
        records.append({"name": name, "request_id": result.get("request_id"), "route": result["control_route"]})
        return result["output"]["context"]

    logs = [open(evidence / "gateway.log", "w"), open(evidence / "adapter.log", "w")]
    processes = []
    try:
        processes.append(
            subprocess.Popen(
                [
                    gateway,
                    "gateway",
                    "--port",
                    "9765",
                    "--remote-port",
                    "0",
                    "--registry-dir",
                    env["DCC_MCP_REGISTRY_DIR"],
                    "--gateway-persist",
                    "true",
                ],
                env=env,
                stdout=logs[0],
                stderr=subprocess.STDOUT,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "dcc_mcp_inkscape",
                    "serve",
                    "--executable",
                    "/usr/bin/inkscape",
                    "--workspace",
                    str(workspace),
                    "--gateway-port",
                    "9765",
                    "--registry-dir",
                    env["DCC_MCP_REGISTRY_DIR"],
                    "--ready-file",
                    str(evidence / "ready.json"),
                ],
                env=env,
                stdout=logs[1],
                stderr=subprocess.STDOUT,
            )
        )
        deadline = time.monotonic() + 30
        while not (evidence / "ready.json").is_file():
            if any(p.poll() is not None for p in processes) or time.monotonic() > deadline:
                raise RuntimeError("Native gateway/controller did not become ready")
            time.sleep(0.2)
        inventory = run_cli("inventory", "list")
        available = [i for i in inventory["instances"] if i["dcc_type"] == "inkscape" and not i.get("stale")]
        if len(available) != 1:
            raise RuntimeError("Expected one owned Inkscape instance")
        instance = available[0]["instance_id"]
        hits = run_cli("discovery", "search", "--query", "inkscape vector", "--dcc-type", "inkscape", "--limit", "10")[
            "hits"
        ]
        selected = [h for h in hits if h["instance_id"] == instance]
        if not selected or selected[0]["next_step"]["action"] != "load_skill":
            raise RuntimeError("Expected a discoverable unloaded native vector skill")
        skill = selected[0]["next_step"]["arguments"]["skill_name"]
        run_cli("load-skill", "load-skill", skill, "--instance-id", instance)
        slugs = {h["backend_tool"].split("__")[-1]: h["tool_slug"] for h in selected}
        for name in ("capabilities", "document_build", "document_inspect", "document_export"):
            run_cli(name + "-schema", "describe", slugs[name])
        capabilities = call("capabilities", slugs["capabilities"], {})
        outputs = []
        for plan_file in sorted(args.plans.glob("*.json")):
            payload = json.loads(plan_file.read_text())
            label = plan_file.stem
            built = call(label + "-build", slugs["document_build"], payload)
            source = payload["output_file"]
            inspected = call(label + "-reopen", slugs["document_inspect"], {"source_file": source})
            base = str(Path(source).with_suffix(""))
            for format_name, suffix, options in [
                ("png", ".png", {"width": 1587}),
                ("pdf", ".pdf", {}),
                ("svg", "-outlined.svg", {"text_to_path": True}),
            ]:
                call(
                    label + suffix.replace(".", "-") + "-export",
                    slugs["document_export"],
                    dict(source_file=source, output_file=base + suffix, format=format_name, **options),
                )
            outlined = call(
                label + "-outlined-reopen", slugs["document_inspect"], {"source_file": base + "-outlined.svg"}
            )
            tree = ElementTree.parse(workspace / source)
            planned = payload["plan"]["nodes"]
            assert {n["id"] for n in planned}.issubset({e.get("id") for e in tree.iter()})
            assert inspected["element_counts"]["text"] == sum(n["type"] == "text" for n in planned)
            assert outlined["element_counts"].get("text", 0) == 0
            outputs.append(
                {
                    "plan": plan_file.name,
                    "planned_objects": len(planned),
                    "native_build": built,
                    "reopen": inspected,
                    "outlined_reopen": outlined,
                }
            )
        fonts = {}
        for family in ("DejaVu Sans", "DejaVu Sans Mono"):
            result = subprocess.run(
                ["fc-match", "-f", "%{file}\n%{family}\n%{style}\n", family],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            file = Path(result.stdout.splitlines()[0])
            fonts[family] = {"fontconfig": result.stdout, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
        for pdf in (workspace / "artwork").glob("*.pdf"):
            for utility in ("pdfinfo", "pdffonts"):
                result = subprocess.run([utility, str(pdf)], capture_output=True, text=True, check=True)
                (evidence / (pdf.stem + "-" + utility + ".txt")).write_text(result.stdout)
        (evidence / "regression-summary.json").write_text(
            json.dumps(
                {
                    "capabilities": capabilities,
                    "outputs": outputs,
                    "fonts": fonts,
                    "calls": records,
                    "visual_acceptance": "not_run",
                    "publication_ready": False,
                    "boundary": "Real native regression artifact; visual review required before showcase submission.",
                },
                indent=2,
            )
        )
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
