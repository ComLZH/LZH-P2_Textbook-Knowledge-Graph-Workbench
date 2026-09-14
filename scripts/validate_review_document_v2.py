from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from textbook_builder.geometry import RenderMetricsProfile, layout_scene
from textbook_builder.readers import DraftWorkbookReader
from textbook_builder.review_document import ExportProfileDTO, ReviewDocumentMigrator
from textbook_builder.review_document_io import ReviewDocumentExporter, ReviewDocumentReader
from textbook_builder.review_views import G6ReviewHtmlRenderer, GraphReviewPayloadBuilder, ReviewProjectionBuilder
from textbook_builder.services import PublishPlanningService


FROZEN_FILES = {
    "draft": (
        PROJECT_ROOT / "导出测试" / "2026-09-08" / "reviewed_draft.xlsx",
        "a2ce02ea65a0c9982fe5d386b10ca02130732a131caff4e12756d120a22e3b2d",
    ),
    "manifest": (
        PROJECT_ROOT / "导出测试" / "2026-09-08" / "export_manifest.json",
        "163e70397cdaae58f9d3f2071ae30391a41d3b05951c2aa80f6ff26faafa6c94",
    ),
    "canonical_nodes": (
        PROJECT_ROOT
        / "progress_recordings"
        / "2026-09-08"
        / "230454_P2-8328981d9b6645aba10b40a80528e40f"
        / "04_node_normalization"
        / "canonical_nodes.json",
        "dc35d36215823f35612d46e6be7e7648c4fed6fb8c705f91ff2d6070d3895df2",
    ),
}


def main() -> int:
    output = PROJECT_ROOT / "storage" / "validation_20260909_review_document_v2"
    output.mkdir(parents=True, exist_ok=True)
    hashes = {name: _sha256(path) for name, (path, _expected) in FROZEN_FILES.items()}
    hash_ok = {
        name: hashes[name] == expected
        for name, (_path, expected) in FROZEN_FILES.items()
    }
    if not all(hash_ok.values()):
        raise RuntimeError("冻结输入哈希变化，停止验收。")
    drafts = DraftWorkbookReader().read(FROZEN_FILES["draft"][0])
    canonical_rows = json.loads(FROZEN_FILES["canonical_nodes"][0].read_text(encoding="utf-8"))
    occurrence_hints = {
        item["node_id"]: {
            "chapter": item.get("chapter", ""),
            "section": item.get("section", ""),
            "temporary_ids": item.get("source_temporary_ids", []),
        }
        for item in canonical_rows
    }
    document = ReviewDocumentMigrator().migrate(
        drafts,
        source_identity="RJ_MATH_G8_2025_AUTUMN",
        analysis_run_id="P2-8328981d9b6645aba10b40a80528e40f",
        occurrence_hints=occurrence_hints,
    )
    review_path = output / "review_document_v2.xlsx"
    base_export_id = ReviewDocumentExporter().export_review_document(document, review_path)
    restored = ReviewDocumentReader().read_document(review_path)
    projection = ReviewProjectionBuilder().build(restored)
    qt_geometry = layout_scene(projection, RenderMetricsProfile(renderer="qt"))
    g6_geometry = layout_scene(projection, RenderMetricsProfile(renderer="g6"))
    payload = GraphReviewPayloadBuilder().build_from_projection(
        projection=projection,
        geometry=g6_geometry,
    )
    html_path = output / "graph_review_v2.html"
    G6ReviewHtmlRenderer().render_to_file(payload=payload, output_path=html_path)
    vendor_source = PROJECT_ROOT / "storage" / "graph_review_static" / "vendor"
    if vendor_source.is_dir():
        shutil.copytree(vendor_source, output / "vendor", dirs_exist_ok=True)
    runtime_dom_path = output / "g6_chrome_dom_final.html"
    screenshot_1480_path = output / "g6_chrome_1480x900_final.png"
    screenshot_1180_path = output / "g6_chrome_1180x720_final.png"
    runtime_geometry = _read_runtime_geometry(
        runtime_dom_path,
        expected_fingerprint=projection.structure_fingerprint,
        expected_revision=restored.metadata.revision,
    )
    plan = PublishPlanningService().preflight(restored, ExportProfileDTO("validation-p4-v1"))
    report = {
        "validation_schema_version": 1,
        "frozen_input_hashes": hashes,
        "frozen_input_hashes_match": hash_ok,
        "review_document": {
            "document_id": restored.metadata.document_id,
            "revision": restored.metadata.revision,
            "base_export_id": base_export_id,
            "round_trip_business_fingerprint_match": (
                restored.business_fingerprint() == document.business_fingerprint()
            ),
            "validation_errors": restored.validate(),
            "node_count": len(restored.nodes),
            "scope_count": len(restored.scopes),
            "occurrence_count": len(restored.occurrences),
            "relation_count": len(restored.relations),
            "relation_families": dict(Counter(item.relation_family for item in restored.relations)),
            "node_statuses": dict(Counter(item.review_status for item in restored.nodes)),
            "relation_statuses": dict(Counter(item.review_status for item in restored.relations)),
        },
        "projection": {
            "scope_count": len(projection.scopes),
            "node_instance_count": len(projection.node_instances),
            "unique_business_node_count": len({item.node_id for item in projection.node_instances}),
            "line_count": len(projection.edge_instances),
            "coverage": projection.relation_coverage(),
        },
        "geometry": {
            "qt_contract_errors": [
                item.code
                for item in qt_geometry.issues
                if item.code.startswith("geometry_") or item.code.endswith("coordinate_missing")
            ],
            "g6_contract_errors": [
                item.code
                for item in g6_geometry.issues
                if item.code.startswith("geometry_") or item.code.endswith("coordinate_missing")
            ],
            "semantic_diagnostics": [
                {"code": item.code, "object_ids": list(item.object_ids)}
                for item in qt_geometry.issues
                if not item.code.startswith("geometry_")
            ],
            "constraint_statuses": dict(
                Counter(item.constraint_status for item in qt_geometry.edge_instances)
            ),
        },
        "runtime_geometry": runtime_geometry,
        "publish_preflight": {
            "status": plan.status,
            "blockers": [
                {"code": item.code, "object_ids": list(item.object_ids)}
                for item in plan.blockers
            ],
            "created_output": False,
        },
        "artifacts": {
            "review_document": str(review_path),
            "graph_html": str(html_path),
            "chrome_dom": str(runtime_dom_path) if runtime_dom_path.is_file() else "",
            "chrome_screenshot_1480x900": (
                str(screenshot_1480_path) if screenshot_1480_path.is_file() else ""
            ),
            "chrome_screenshot_1180x720": (
                str(screenshot_1180_path) if screenshot_1180_path.is_file() else ""
            ),
        },
    }
    report_path = output / "validation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_runtime_geometry(
    dom_path: Path,
    *,
    expected_fingerprint: str,
    expected_revision: int,
) -> dict[str, object]:
    if not dom_path.is_file():
        return {"status": "not_run"}
    raw = dom_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r'<pre id="geometryReportJson"[^>]*>(.*?)</pre>',
        raw,
        flags=re.DOTALL,
    )
    if match is None:
        return {"status": "missing_report", "dom_path": str(dom_path)}
    payload = json.loads(html.unescape(match.group(1)))
    is_current = (
        payload.get("structureFingerprint") == expected_fingerprint
        and payload.get("documentRevision") == expected_revision
    )
    return {
        "status": "passed" if is_current and not payload.get("violations") else "failed",
        "is_current": is_current,
        "ready": bool(payload.get("ready")),
        "node_count": len(payload.get("nodes", {})),
        "scope_count": len(payload.get("combos", {})),
        "violation_count": len(payload.get("violations", [])),
        "violations": payload.get("violations", []),
        "measurement_coordinate_system": payload.get("measurementCoordinateSystem", ""),
        "tolerance": payload.get("tolerance"),
        "dom_path": str(dom_path),
    }


if __name__ == "__main__":
    raise SystemExit(main())
