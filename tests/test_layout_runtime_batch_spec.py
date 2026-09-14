from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from textbook_builder.geometry import Rect, RenderMetricsProfile
from textbook_builder.geometry import elk_backend
from textbook_builder.geometry.elk_backend import ElkLayoutJob, layout_graph_batch


def test_component_jobs_share_one_process_and_validate_each_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        elk_backend,
        "runtime_status",
        lambda _root=None: {
            "root": str(runtime_root),
            "available": True,
            "node": str(runtime_root / "node.exe"),
            "elk": str(runtime_root / "elk.js"),
            "runner": str(runtime_root / "runner.mjs"),
        },
    )

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        input_path = Path(args[-2])
        output_path = Path(args[-1])
        request = json.loads(input_path.read_text(encoding="utf-8"))
        results = []
        for job in request["component_jobs"]:
            graph = job["graph"]
            graph["children"] = [
                {**node, "x": float(index * 300), "y": 0.0}
                for index, node in enumerate(graph["children"])
            ]
            results.append(
                {
                    "component_id": job["component_id"],
                    "status": "completed",
                    "elapsed_ms": 1.0,
                    "graph": graph,
                }
            )
        output_path.write_text(
            json.dumps(
                {
                    "protocol_version": "p2_elk_batch_v1",
                    "request_id": request["request_id"],
                    "complete": True,
                    "elapsed_ms": 2.0,
                    "results": results,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(elk_backend.subprocess, "run", fake_run)
    jobs = [
        ElkLayoutJob("component-a", {"a": Rect(0, 0, 250, 86)}, ()),
        ElkLayoutJob("component-b", {"b": Rect(0, 0, 250, 86)}, ()),
    ]

    result = layout_graph_batch(jobs, RenderMetricsProfile(renderer="test"))

    assert len(calls) == 1
    assert result.process_count == 1
    assert result.batch_count == 1
    assert set(result.component_results) == {"component-a", "component-b"}
    assert result.failures == {}


def test_timeout_checkpoint_preserves_completed_components(tmp_path: Path, monkeypatch) -> None:
    _stub_runtime(tmp_path, monkeypatch)

    def fake_run(args, **_kwargs):
        request = json.loads(Path(args[-2]).read_text(encoding="utf-8"))
        Path(args[-1]).write_text(
            json.dumps(_batch_response(request, completed_ids={"component-a"}, complete=False)),
            encoding="utf-8",
        )
        raise elk_backend.subprocess.TimeoutExpired(args, timeout=0.1)

    monkeypatch.setattr(elk_backend.subprocess, "run", fake_run)
    result = layout_graph_batch(_two_jobs(), RenderMetricsProfile(renderer="test"))

    assert set(result.component_results) == {"component-a"}
    assert result.failures == {"component-b": "runtime_timeout_unfinished"}
    assert "runtime_timeout_partial" in result.diagnostics


def test_late_runtime_exit_preserves_valid_checkpoint_and_rejects_unknown_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _stub_runtime(tmp_path, monkeypatch)

    def fake_run(args, **_kwargs):
        request = json.loads(Path(args[-2]).read_text(encoding="utf-8"))
        response = _batch_response(request, completed_ids={"component-a"}, complete=False)
        response["results"].append(
            {
                "component_id": "not-requested",
                "status": "completed",
                "elapsed_ms": 1.0,
                "graph": request["component_jobs"][0]["graph"],
            }
        )
        Path(args[-1]).write_text(json.dumps(response), encoding="utf-8")
        return SimpleNamespace(returncode=13)

    monkeypatch.setattr(elk_backend.subprocess, "run", fake_run)
    result = layout_graph_batch(_two_jobs(), RenderMetricsProfile(renderer="test"))

    assert set(result.component_results) == {"component-a"}
    assert result.failures == {"component-b": "runtime_exit_13_unfinished"}
    assert "batch_component_identity_invalid" in result.diagnostics
    assert "runtime_exit_13" in result.diagnostics


def _stub_runtime(tmp_path: Path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(
        elk_backend,
        "runtime_status",
        lambda _root=None: {
            "root": str(runtime_root),
            "available": True,
            "node": str(runtime_root / "node.exe"),
            "elk": str(runtime_root / "elk.js"),
            "runner": str(runtime_root / "runner.mjs"),
        },
    )


def _two_jobs() -> list[ElkLayoutJob]:
    return [
        ElkLayoutJob("component-a", {"a": Rect(0, 0, 250, 86)}, ()),
        ElkLayoutJob("component-b", {"b": Rect(0, 0, 250, 86)}, ()),
    ]


def _batch_response(
    request: dict[str, object],
    *,
    completed_ids: set[str],
    complete: bool,
) -> dict[str, object]:
    records = []
    for job in request["component_jobs"]:
        if job["component_id"] not in completed_ids:
            continue
        graph = job["graph"]
        graph["children"] = [
            {**node, "x": float(index * 300), "y": 0.0}
            for index, node in enumerate(graph["children"])
        ]
        records.append(
            {
                "component_id": job["component_id"],
                "status": "completed",
                "elapsed_ms": 1.0,
                "graph": graph,
            }
        )
    return {
        "protocol_version": "p2_elk_batch_v1",
        "request_id": request["request_id"],
        "complete": complete,
        "elapsed_ms": 2.0,
        "results": records,
    }
