from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import tracemalloc
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from textbook_builder.review_document_io import ReviewDocumentReader  # noqa: E402
from textbook_builder.review_views import (  # noqa: E402
    G6ReviewHtmlRenderer,
    GraphReviewPayloadBuilder,
    VIEW_MODE_RELATIONS,
    VIEW_MODE_TEXTBOOK,
)
from textbook_builder.services import WorkbenchReviewService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="对真实复核文档执行冷启动与热运行的端到端图谱基准。"
    )
    parser.add_argument("--input", nargs="+", required=True, help="一个或多个 review_document_v2.xlsx")
    parser.add_argument("--output", required=True, help="新的 JSON 报告路径；拒绝覆盖")
    parser.add_argument(
        "--mode",
        choices=(VIEW_MODE_RELATIONS, VIEW_MODE_TEXTBOOK),
        default=VIEW_MODE_RELATIONS,
    )
    parser.add_argument("--cold-runs", type=int, default=5)
    parser.add_argument("--warm-runs", type=int, default=20)
    parser.add_argument("--p95-limit-ms", type=float, default=5000.0)
    args = parser.parse_args()

    output_path = Path(args.output).resolve()
    if output_path.exists():
        parser.error(f"输出文件已存在，拒绝覆盖：{output_path}")
    if args.cold_runs < 1 or args.warm_runs < 1:
        parser.error("冷启动和热运行次数均至少为 1。")
    input_paths = [Path(value).resolve() for value in args.input]
    missing = [path for path in input_paths if not path.is_file()]
    if missing:
        parser.error(f"输入文件不存在：{missing[0]}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, object]] = []
    all_passed = True
    for input_path in input_paths:
        cold_timings: list[float] = []
        last_bundle = None
        for _index in range(args.cold_runs):
            started = time.perf_counter()
            document = ReviewDocumentReader().read_document(input_path)
            last_bundle = _render_once(document, WorkbenchReviewService(), args.mode)
            cold_timings.append(round((time.perf_counter() - started) * 1000.0, 3))

        document = ReviewDocumentReader().read_document(input_path)
        warm_service = WorkbenchReviewService()
        warm_timings: list[float] = []
        for _index in range(args.warm_runs):
            started = time.perf_counter()
            last_bundle = _render_once(document, warm_service, args.mode)
            warm_timings.append(round((time.perf_counter() - started) * 1000.0, 3))
        assert last_bundle is not None

        tracemalloc.start()
        memory_started = time.perf_counter()
        memory_bundle = _render_once(document, WorkbenchReviewService(), args.mode)
        memory_elapsed_ms = round((time.perf_counter() - memory_started) * 1000.0, 3)
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        report = last_bundle.geometry.readability
        cold_p95 = _percentile(cold_timings, 0.95)
        warm_p95 = _percentile(warm_timings, 0.95)
        passed = (
            report.hard_violations == 0
            and memory_bundle.geometry.readability.hard_violations == 0
            and cold_p95 <= args.p95_limit_ms
            and warm_p95 <= args.p95_limit_ms
        )
        all_passed = all_passed and passed
        cases.append(
            {
                "input": str(input_path),
                "sha256": _sha256(input_path),
                "document_id": document.metadata.document_id,
                "document_revision": document.metadata.revision,
                "mode": args.mode,
                "nodes": len(last_bundle.projection.node_instances),
                "edges": len(last_bundle.projection.edge_instances),
                "engine": last_bundle.geometry.engine,
                "cold_elapsed_ms_wall": cold_timings,
                "cold_p50_ms": _percentile(cold_timings, 0.50),
                "cold_p95_ms": cold_p95,
                "warm_elapsed_ms_wall": warm_timings,
                "warm_p50_ms": _percentile(warm_timings, 0.50),
                "warm_p95_ms": warm_p95,
                "memory_probe_elapsed_ms_wall": memory_elapsed_ms,
                "peak_tracemalloc_bytes": peak_bytes,
                "canvas": asdict(last_bundle.geometry.canvas_rect),
                "readability": asdict(report),
                "passed": passed,
            }
        )

    result = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "measurement": {
            "cold": "read workbook + create service + layout + payload + HTML serialization",
            "warm": "preloaded workbook + reused service + layout + payload + HTML serialization",
            "memory": "Python tracemalloc peak; child Node process RSS is excluded",
            "cold_runs": args.cold_runs,
            "warm_runs": args.warm_runs,
            "p95_limit_ms": args.p95_limit_ms,
        },
        "cases": cases,
        "all_passed": all_passed,
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all_passed else 2


def _render_once(document, service: WorkbenchReviewService, mode: str):
    bundle = service.build_review_render_bundle(
        document,
        renderer="g6-benchmark",
        view_mode=mode,
    )
    payload = GraphReviewPayloadBuilder().build_from_projection(
        projection=bundle.projection,
        geometry=bundle.geometry,
        document=document,
    )
    G6ReviewHtmlRenderer().render(payload=payload)
    return bundle


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 3)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
