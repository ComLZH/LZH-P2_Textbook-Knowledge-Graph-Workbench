from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from .contracts import DraftKnowledgeItemDTO, SourceRecordDTO
from .exporters import FormalGraphWorkbookBuilder, XlsxWorkbookExporter
from .llm import LlmCandidatePayloadParser
from .readers import DocumentReadOptions, PdfPageRange, SourceDocumentReader
from .utils.subject_metadata import derive_subject_metadata, missing_required_metadata
from .utils.review_status import normalize_review_status
from .review_document import ExportProfileDTO
from .services import ReviewDocumentCommand, ReviewDocumentSession, WorkbenchReviewService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKBENCH_STORAGE = PROJECT_ROOT / "storage" / "workbench"
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}


app = FastAPI(title="Textbook Knowledge Graph Workbench")
_sessions: dict[str, dict[str, Any]] = {}


class AnalyzeRequest(BaseModel):
    session_id: str
    start_page: int | None = None
    end_page: int | None = None
    label: str = "manual_scope"


class ReviewUpdateRequest(BaseModel):
    session_id: str
    item_type: str
    item_id: str
    review_status: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return WORKBENCH_HTML


@app.get("/workbench", response_class=HTMLResponse)
def workbench() -> str:
    return WORKBENCH_HTML


@app.post("/api/upload")
def upload_source(
    file: UploadFile = File(...),
    subject: str = Form(...),
    grade: str = Form(...),
    term: str = Form(...),
    source_id: str = Form(""),
) -> dict[str, Any]:
    missing = missing_required_metadata(subject, grade, term)
    if missing:
        raise HTTPException(
            status_code=422,
            detail="以下教材元数据必须显式提供：" + "、".join(missing),
        )
    metadata = derive_subject_metadata(subject, grade, term)
    subject, grade, term = metadata.subject, metadata.grade, metadata.term
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Only PDF, PNG, JPG, and JPEG are supported.")
    session_id = uuid4().hex
    session_dir = WORKBENCH_STORAGE / session_id
    upload_dir = session_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or f"source{suffix}").name
    source_path = upload_dir / safe_name
    with source_path.open("wb") as stream:
        shutil.copyfileobj(file.file, stream)

    options = _document_options(
        subject=subject,
        grade=grade,
        term=term,
        source_id=source_id or source_path.stem,
    )
    record = SourceDocumentReader().read(source_path, options)[0]
    _sessions[session_id] = {
        "session_id": session_id,
        "session_dir": session_dir,
        "source_path": source_path,
        "subject": subject,
        "grade": grade,
        "term": term,
        "source_id": source_id or source_path.stem,
        "base_record": record,
        "selected_record": None,
        "drafts": [],
        "workbook": None,
        "review_document_session": None,
    }
    return {
        "session_id": session_id,
        "file_name": safe_name,
        "file_url": f"/api/files/{session_id}/{safe_name}",
        "source_format": record.source_format,
        "source_metadata": record.source_metadata,
    }


@app.get("/api/files/{session_id}/{file_name}")
def get_uploaded_file(session_id: str, file_name: str) -> FileResponse:
    session = _get_session(session_id)
    path = session["session_dir"] / "uploads" / Path(file_name).name
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path)


@app.post("/api/analyze")
def analyze_scope(payload: AnalyzeRequest) -> dict[str, Any]:
    session = _get_session(payload.session_id)
    source_path = Path(session["source_path"])
    source_format = source_path.suffix.lower().lstrip(".")
    options = _document_options(
        subject=session["subject"],
        grade=session["grade"],
        term=session["term"],
        source_id=session["source_id"],
    )
    if source_format == "pdf":
        start_page = payload.start_page or 1
        end_page = payload.end_page or start_page
        options.pdf_page_ranges = [
            PdfPageRange(start_page=start_page, end_page=end_page, label=payload.label)
        ]
    records = SourceDocumentReader().read(source_path, options)
    record = records[0]
    drafts = LlmCandidatePayloadParser().parse(
        json.dumps(_offline_llm_payload(record), ensure_ascii=False),
        record=record,
    )
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id=f"REVIEW_{record.source_id}",
        textbook_version="local_workbench_v1",
    )
    session["selected_record"] = record
    document = WorkbenchReviewService().create_review_document(
        drafts,
        source_identity=record.source_id,
    )
    document_session = ReviewDocumentSession(document)
    session["review_document_session"] = document_session
    session["drafts"] = document_session.draft_rows()
    session["workbook"] = workbook
    return _state_payload(session)


@app.post("/api/review")
def update_review(payload: ReviewUpdateRequest) -> dict[str, Any]:
    session = _get_session(payload.session_id)
    document_session: ReviewDocumentSession | None = session.get("review_document_session")
    if document_session is None:
        raise HTTPException(status_code=409, detail="Session has no schema-v2 review document.")
    status = normalize_review_status(payload.review_status)
    if payload.item_type == "node":
        if document_session.snapshot().node_by_id(payload.item_id) is None:
            raise HTTPException(status_code=404, detail="Node candidate not found.")
        command = ReviewDocumentCommand(
            "review_node", "node", payload.item_id, {"review_status": status}
        )
    elif payload.item_type == "edge":
        document = document_session.snapshot()
        relation = next(
            (
                item
                for item in document.relations
                if _relation_id(
                    {
                        "source_node_id": item.source_node_id,
                        "target_node_id": item.target_node_id,
                        "relation_type": item.relation_type,
                    },
                    item.source_node_id,
                )
                == payload.item_id
                or item.relation_id == payload.item_id
            ),
            None,
        )
        if relation is None:
            raise HTTPException(status_code=404, detail="Editable relation candidate not found.")
        command = ReviewDocumentCommand(
            "review_relation", "relation", relation.relation_id, {"review_status": status}
        )
    else:
        raise HTTPException(status_code=400, detail="item_type must be node or edge.")
    document_session.execute(command, expected_revision=document_session.revision)
    drafts = document_session.draft_rows()
    session["drafts"] = drafts
    session["workbook"] = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id=f"REVIEW_{session['selected_record'].source_id}",
        textbook_version="local_workbench_v1",
    )
    return _state_payload(session)


@app.post("/api/export")
def export_reviewed(
    session_id: str = Form(...),
    primary_memberships_json: str = Form("{}"),
) -> dict[str, Any]:
    session = _get_session(session_id)
    document_session: ReviewDocumentSession | None = session.get("review_document_session")
    if document_session is None:
        raise HTTPException(status_code=400, detail="No candidates to export.")
    export_dir = session["session_dir"] / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    service = WorkbenchReviewService()
    try:
        primary_memberships = json.loads(primary_memberships_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="primary_memberships_json is invalid JSON.") from exc
    if not isinstance(primary_memberships, dict):
        raise HTTPException(status_code=400, detail="primary_memberships_json must be an object.")
    if primary_memberships:
        document_session.execute(
            ReviewDocumentCommand(
                "confirm_publish_profile",
                "profile",
                "web-p4-v1",
                {
                    "target_protocol": "p4_v1_single_membership",
                    "primary_membership_by_node": {
                        str(key): str(value) for key, value in primary_memberships.items()
                    },
                },
            ),
            expected_revision=document_session.revision,
        )
    document = document_session.snapshot()
    review_path = export_dir / "review_document_v2.xlsx"
    save_result = service.save_review_document(document, review_path)
    profile = document.profile_by_id("web-p4-v1") or ExportProfileDTO("web-p4-v1")
    plan = service.preflight_publish(document, profile)
    publish_result = None
    if plan.ready:
        publish_result = service.publish(
            document,
            plan,
            expected_revision=document.metadata.revision,
            output_root=export_dir / "published",
        )
    return {
        "review_path": str(review_path),
        "base_export_id": save_result.base_export_id,
        "preflight_status": plan.status,
        "blockers": [asdict(item) for item in plan.blockers],
        "suggested_primary_memberships": plan.suggested_primary_membership_by_node,
        "formal_path": str(publish_result.formal_path or "") if publish_result else "",
        "manifest_path": str(publish_result.manifest_path or "") if publish_result else "",
        "accepted_node_count": len(plan.selected_node_ids),
    }


def run() -> None:
    import uvicorn

    WORKBENCH_STORAGE.mkdir(parents=True, exist_ok=True)
    uvicorn.run("textbook_builder.web_app:app", host="127.0.0.1", port=8008, reload=False)


def _get_session(session_id: str) -> dict[str, Any]:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


def _document_options(
    *,
    subject: str,
    grade: str,
    term: str,
    source_id: str,
) -> DocumentReadOptions:
    metadata = derive_subject_metadata(subject, grade, term)
    return DocumentReadOptions(
        subject=subject,
        grade=grade,
        term=term,
        source_id=source_id,
        source_document_type="electronic_textbook",
        education_stage=metadata.education_stage,
        grade_band=metadata.grade_band,
        subject_tags=list(metadata.subject_tags),
    )


def _state_payload(session: dict[str, Any]) -> dict[str, Any]:
    record: SourceRecordDTO | None = session.get("selected_record")
    drafts: list[DraftKnowledgeItemDTO] = session.get("drafts", [])
    workbook = session.get("workbook")
    return {
        "session_id": session["session_id"],
        "selected_record": asdict(record) if record else None,
        "nodes": _nodes_payload(drafts),
        "edges": _edges_payload(drafts, workbook),
        "anchors": _anchors_payload(drafts),
        "graph": _graph_payload(workbook) if workbook else None,
    }


def _nodes_payload(drafts: list[DraftKnowledgeItemDTO]) -> list[dict[str, Any]]:
    return [
        {
            "id": draft.candidate_node_id,
            "name": draft.candidate_display_name,
            "type": draft.knowledge_type,
            "chapter": draft.chapter,
            "status": draft.review_status,
            "confidence": draft.confidence,
            "reason": draft.reasoning_summary,
            "source_text": draft.source_text,
            "source_location": draft.source_location,
        }
        for draft in drafts
    ]


def _edges_payload(drafts: list[DraftKnowledgeItemDTO], workbook: Any) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    node_names = {draft.candidate_node_id: draft.candidate_display_name for draft in drafts}
    if workbook:
        for edge in workbook.edges:
            edge_id = f"{edge.source_node_id}--{edge.relation_type}--{edge.target_node_id}"
            editable = edge.relation_source == "candidate_relations"
            edges.append(
                {
                    "id": edge_id,
                    "source": edge.source_node_id,
                    "target": edge.target_node_id,
                    "source_name": node_names.get(edge.source_node_id, edge.source_node_id),
                    "target_name": node_names.get(edge.target_node_id, edge.target_node_id),
                    "type": edge.relation_type,
                    "status": edge.review_status,
                    "evidence": edge.relation_evidence,
                    "editable": editable,
                }
            )
    return edges


def _anchors_payload(drafts: list[DraftKnowledgeItemDTO]) -> list[dict[str, Any]]:
    anchors = []
    for draft in drafts:
        for anchor in draft.evidence_anchors:
            anchors.append(
                {
                    "id": anchor.anchor_id,
                    "target_ids": anchor.target_ids,
                    "text": anchor.anchor_text,
                    "location": anchor.source_location,
                    "status": anchor.review_status,
                    "created_by": anchor.created_by,
                    "node_name": draft.candidate_display_name,
                }
            )
    return anchors


def _graph_payload(workbook: Any) -> dict[str, Any]:
    nodes = []
    edges = []
    if not workbook:
        return {"nodes": [], "edges": []}
    children_by_parent: dict[str, list[str]] = {}
    for node in workbook.nodes:
        if node.parent_node_id:
            children_by_parent.setdefault(node.parent_node_id, []).append(node.node_id)
    chapter_nodes = [
        node for node in workbook.nodes if node.node_type == "container" or node.knowledge_type == "container"
    ]
    positions: dict[str, tuple[int, int]] = {}
    current_y = 40
    for chapter in chapter_nodes or workbook.nodes[:1]:
        child_ids = children_by_parent.get(chapter.node_id, [])
        positions[chapter.node_id] = (60, current_y)
        for index, child_id in enumerate(child_ids):
            positions[child_id] = (260 + index * 220, current_y)
        current_y += 150
    for index, node in enumerate(workbook.nodes):
        positions.setdefault(node.node_id, (80 + index * 210, current_y))
    for node in workbook.nodes:
        x, y = positions[node.node_id]
        nodes.append(
            {
                "id": node.node_id,
                "name": node.display_name,
                "type": node.knowledge_type or node.node_type,
                "x": x,
                "y": y,
            }
        )
    for edge in workbook.edges:
        edges.append(
            {
                "id": f"{edge.source_node_id}--{edge.relation_type}--{edge.target_node_id}",
                "source": edge.source_node_id,
                "target": edge.target_node_id,
                "type": edge.relation_type,
                "status": edge.review_status,
            }
        )
    return {"nodes": nodes, "edges": edges}


def _relation_id(relation: dict[str, object], fallback_source_id: str) -> str:
    source = str(relation.get("source_node_id") or fallback_source_id)
    target = str(relation.get("target_node_id") or "")
    relation_type = str(relation.get("relation_type") or "")
    return f"{source}--{relation_type}--{target}"


def _offline_llm_payload(record: SourceRecordDTO) -> dict[str, object]:
    source_location = "page=selected"
    selected_pages = record.source_metadata.get("selected_pages")
    if isinstance(selected_pages, list) and selected_pages:
        source_location = f"page={selected_pages[0]}-{selected_pages[-1]}"
    if record.source_format in {"png", "jpg", "jpeg"}:
        source_location = "image=1"
    return {
        "chapters": [
            {
                "chapter": "ch_local",
                "title": "本地导入材料",
                "knowledge_points": [
                    {
                        "candidate_display_name": "材料中的核心概念",
                        "candidate_node_name": "core_concept",
                        "knowledge_type": "concept",
                        "cognitive_level": "understand",
                        "source_text": record.raw_text or "由教师导入的局部教材材料，需要结合页面内容确认核心概念。",
                        "source_location": source_location,
                        "confidence": 0.72,
                        "reasoning_summary": "第一版本地程序使用离线候选生成，用于验证页面审查闭环；后续可替换为真实大模型返回。",
                        "review_status": "pending",
                    },
                    {
                        "candidate_display_name": "核心概念的应用关系",
                        "candidate_node_name": "core_concept_application",
                        "knowledge_type": "property",
                        "cognitive_level": "apply",
                        "source_text": record.raw_text or "请教师根据导入材料页面确认该候选关系是否成立。",
                        "source_location": source_location,
                        "confidence": 0.68,
                        "reasoning_summary": "该候选用于演示从概念到应用或性质的关系审查。",
                        "review_status": "pending",
                        "candidate_relations": [
                            {
                                "source_node_id": f"{record.subject}_{record.grade}_{record.term}_ch_local_core_concept",
                                "target_node_id": f"{record.subject}_{record.grade}_{record.term}_ch_local_core_concept_application",
                                "relation_type": "progressive",
                                "confidence": 0.68,
                                "relation_evidence": "从核心概念进入应用或性质判断，暂作为递进关系候选。",
                                "reasoning_summary": "需要教师根据教材页面确认。",
                                "review_status": "pending",
                            }
                        ],
                    },
                ],
            }
        ]
    }


WORKBENCH_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>教材知识图谱本地工作台</title>
  <style>
    :root { --ink:#1f2937; --muted:#667085; --line:#d0d5dd; --panel:#fff; --bg:#eef2f6; --accent:#0f766e; --warn:#b54708; --ok:#15803d; --bad:#b42318; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:"Segoe UI","Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }
    header { height:58px; display:flex; align-items:center; justify-content:space-between; padding:0 18px; background:#fff; border-bottom:1px solid var(--line); }
    h1 { font-size:18px; margin:0; }
    main { height:calc(100vh - 58px); display:grid; grid-template-columns: 360px 1fr; gap:12px; padding:12px; }
    aside, section { background:var(--panel); border:1px solid var(--line); border-radius:8px; min-height:0; overflow:hidden; }
    aside { display:flex; flex-direction:column; }
    .controls { padding:12px; overflow:auto; border-bottom:1px solid var(--line); }
    label { display:block; font-size:12px; color:var(--muted); margin:10px 0 4px; }
    input, select, button { font:inherit; }
    input[type="text"], input[type="number"], input[type="file"] { width:100%; padding:8px; border:1px solid var(--line); border-radius:6px; background:#fff; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    button { border:1px solid var(--line); border-radius:6px; padding:8px 10px; background:#fff; cursor:pointer; }
    button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .status { padding:10px 12px; font-size:12px; color:var(--muted); line-height:1.5; }
    .workspace { display:grid; grid-template-rows: 46% 54%; gap:12px; min-width:0; }
    .top { display:grid; grid-template-columns: 44% 56%; gap:12px; min-height:0; }
    .panel-title { padding:10px 12px; border-bottom:1px solid var(--line); font-weight:700; font-size:14px; background:#fbfcfd; }
    .panel-body { height:calc(100% - 41px); overflow:auto; }
    object, img { width:100%; height:100%; border:0; background:#f8fafc; }
    img { object-fit:contain; padding:8px; }
    svg { width:100%; min-height:360px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { border-bottom:1px solid #edf0f3; padding:8px; text-align:left; vertical-align:top; }
    th { position:sticky; top:0; background:#f8fafc; color:var(--muted); z-index:1; }
    .pill { padding:2px 7px; border-radius:999px; background:#f2f4f7; font-size:12px; }
    .pill.pending { background:#fef0c7; color:var(--warn); }
    .pill.accepted { background:#dcfce7; color:var(--ok); }
    .pill.rejected { background:#fee4e2; color:var(--bad); }
    .tabs { display:flex; gap:8px; padding:8px; border-bottom:1px solid var(--line); }
    .tab { padding:5px 9px; font-size:12px; }
    .tab.active { background:#e6fffb; border-color:#99f6e4; }
    .hidden { display:none; }
    .node rect { fill:#324a5f; rx:7; }
    .node text { fill:white; font-size:12px; pointer-events:none; }
    .edge { stroke:#667085; stroke-width:2; fill:none; marker-end:url(#arrow); }
    .edge.progressive { stroke:#0f766e; }
    .edge.contains { stroke:#c5a14a; stroke-dasharray:5 4; }
    .edge-label { font-size:11px; fill:#475467; paint-order:stroke; stroke:#fff; stroke-width:4px; }
    .empty { padding:18px; color:var(--muted); line-height:1.7; }
  </style>
</head>
<body>
  <header>
    <h1>教材知识图谱本地工作台</h1>
    <div id="sessionLabel" class="status">未导入文件</div>
  </header>
  <main>
    <aside>
      <div class="controls">
        <label>导入教材文件（PDF / PNG / JPG）</label>
        <input id="fileInput" type="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg">
        <div class="row">
          <div><label>学科（必填）</label><input id="subjectInput" type="text" placeholder="例如 art / math" required></div>
          <div><label>年级（必填）</label><input id="gradeInput" type="text" placeholder="例如 g7" required></div>
        </div>
        <div class="row">
          <div><label>册次（必填）</label><input id="termInput" type="text" placeholder="例如 term1" required></div>
          <div><label>来源 ID</label><input id="sourceIdInput" type="text" value="LOCAL_TEXTBOOK"></div>
        </div>
        <button class="primary" style="margin-top:12px;width:100%" onclick="uploadFile()">导入文件</button>
        <div class="row">
          <div><label>起始 PDF 页</label><input id="startPageInput" type="number" min="1" value="1"></div>
          <div><label>结束 PDF 页</label><input id="endPageInput" type="number" min="1" value="8"></div>
        </div>
        <label>范围名称</label><input id="labelInput" type="text" value="chapter_scope">
        <button id="analyzeBtn" class="primary" style="margin-top:12px;width:100%" onclick="analyze()" disabled>送入大模型分析（本地模拟）</button>
        <button id="exportBtn" style="margin-top:8px;width:100%" onclick="exportReviewed()" disabled>导出审查结果</button>
      </div>
      <div id="statusBox" class="status">请先导入教材文件。</div>
    </aside>
    <div class="workspace">
      <div class="top">
        <section>
          <div class="panel-title">教材原文预览</div>
          <div id="preview" class="panel-body"><div class="empty">导入 PDF 或图片后将在这里预览。</div></div>
        </section>
        <section>
          <div class="panel-title">知识图谱候选</div>
          <div id="graph" class="panel-body"><div class="empty">点击“送入大模型分析”后生成候选图谱。</div></div>
        </section>
      </div>
      <section>
        <div class="tabs">
          <button id="nodeTab" class="tab active" onclick="showTab('nodes')">节点审查</button>
          <button id="edgeTab" class="tab" onclick="showTab('edges')">关系审查</button>
          <button id="anchorTab" class="tab" onclick="showTab('anchors')">证据锚点</button>
        </div>
        <div id="nodesPanel" class="panel-body"></div>
        <div id="edgesPanel" class="panel-body hidden"></div>
        <div id="anchorsPanel" class="panel-body hidden"></div>
      </section>
    </div>
  </main>
  <script>
    let sessionId = "";
    let sourceFormat = "";
    let currentState = null;

    function setStatus(text) { document.getElementById('statusBox').innerText = text; }

    async function uploadFile() {
      const file = document.getElementById('fileInput').files[0];
      if (!file) { setStatus('请选择文件。'); return; }
      const subject = document.getElementById('subjectInput').value.trim();
      const grade = document.getElementById('gradeInput').value.trim();
      const term = document.getElementById('termInput').value.trim();
      if (!subject || !grade || !term) {
        setStatus('请明确填写学科、年级和册次；系统不会自动补成数学。');
        return;
      }
      const form = new FormData();
      form.append('file', file);
      form.append('subject', subject);
      form.append('grade', grade);
      form.append('term', term);
      form.append('source_id', document.getElementById('sourceIdInput').value || file.name.replace(/\W+/g, '_'));
      setStatus('正在导入文件...');
      const res = await fetch('/api/upload', { method:'POST', body:form });
      if (!res.ok) { setStatus('导入失败：' + await res.text()); return; }
      const data = await res.json();
      sessionId = data.session_id;
      sourceFormat = data.source_format;
      document.getElementById('sessionLabel').innerText = '会话 ' + sessionId.slice(0, 8);
      document.getElementById('analyzeBtn').disabled = false;
      renderPreview(data.file_url, data.source_format);
      setStatus('已导入：' + data.file_name + '\\n格式：' + data.source_format + '\\n页数/尺寸：' + JSON.stringify(data.source_metadata));
    }

    function renderPreview(url, format) {
      const preview = document.getElementById('preview');
      if (format === 'pdf') {
        preview.innerHTML = '<object data="' + url + '" type="application/pdf"></object>';
      } else {
        preview.innerHTML = '<img src="' + url + '" alt="source image">';
      }
    }

    async function analyze() {
      if (!sessionId) return;
      setStatus('正在生成候选图谱...');
      const body = {
        session_id: sessionId,
        start_page: parseInt(document.getElementById('startPageInput').value || '1'),
        end_page: parseInt(document.getElementById('endPageInput').value || '1'),
        label: document.getElementById('labelInput').value || 'manual_scope'
      };
      const res = await fetch('/api/analyze', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      if (!res.ok) { setStatus('分析失败：' + await res.text()); return; }
      currentState = await res.json();
      document.getElementById('exportBtn').disabled = false;
      renderState(currentState);
      setStatus('候选图谱已生成。你可以在下方表格中修改审查状态。');
    }

    function renderState(state) {
      renderGraph(state.graph);
      renderNodes(state.nodes);
      renderEdges(state.edges);
      renderAnchors(state.anchors);
    }

    function renderGraph(graph) {
      if (!graph || !graph.nodes.length) {
        document.getElementById('graph').innerHTML = '<div class="empty">暂无图谱。</div>';
        return;
      }
      const nodeById = {};
      graph.nodes.forEach(n => nodeById[n.id] = n);
      let svg = '<svg viewBox="0 0 980 420"><defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#667085"></path></marker></defs>';
      graph.edges.forEach(e => {
        const s = nodeById[e.source], t = nodeById[e.target];
        if (!s || !t) return;
        const sx = s.x + 150, sy = s.y + 28, tx = t.x, ty = t.y + 28;
        const mx = (sx + tx) / 2;
        svg += '<path class="edge ' + e.type + '" d="M ' + sx + ' ' + sy + ' C ' + mx + ' ' + sy + ', ' + mx + ' ' + ty + ', ' + tx + ' ' + ty + '"></path>';
        svg += '<text class="edge-label" x="' + mx + '" y="' + (sy - 8) + '">' + escapeHtml(e.type) + '</text>';
      });
      graph.nodes.forEach(n => {
        svg += '<g class="node" transform="translate(' + n.x + ',' + n.y + ')"><rect width="150" height="56"></rect><text x="10" y="24">' + escapeHtml(clip(n.name, 12)) + '</text><text x="10" y="43">' + escapeHtml(n.type) + '</text></g>';
      });
      svg += '</svg>';
      document.getElementById('graph').innerHTML = svg;
    }

    function renderNodes(nodes) {
      let html = '<table><thead><tr><th>节点</th><th>类型</th><th>状态</th><th>候选理由</th><th>操作</th></tr></thead><tbody>';
      nodes.forEach(n => {
        html += '<tr><td>' + escapeHtml(n.name) + '<br><small>' + escapeHtml(n.id) + '</small></td><td>' + escapeHtml(n.type) + '</td><td><span class="pill ' + n.status + '">' + n.status + '</span></td><td>' + escapeHtml(n.reason || '') + '</td><td>' + statusButtons('node', n.id) + '</td></tr>';
      });
      html += '</tbody></table>';
      document.getElementById('nodesPanel').innerHTML = html;
    }

    function renderEdges(edges) {
      let html = '<table><thead><tr><th>关系</th><th>状态</th><th>证据</th><th>操作</th></tr></thead><tbody>';
      edges.forEach(e => {
        const ops = e.editable ? statusButtons('edge', e.id) : '<span class="pill">derived</span>';
        html += '<tr><td>' + escapeHtml(e.source_name) + ' -> ' + escapeHtml(e.target_name) + '<br><small>' + escapeHtml(e.type) + '</small></td><td><span class="pill ' + e.status + '">' + e.status + '</span></td><td>' + escapeHtml(e.evidence || '') + '</td><td>' + ops + '</td></tr>';
      });
      html += '</tbody></table>';
      document.getElementById('edgesPanel').innerHTML = html;
    }

    function renderAnchors(anchors) {
      let html = '<table><thead><tr><th>锚点</th><th>位置</th><th>来源</th><th>状态</th></tr></thead><tbody>';
      anchors.forEach(a => {
        html += '<tr><td>' + escapeHtml(a.node_name) + '<br><small>' + escapeHtml(clip(a.text || '', 80)) + '</small></td><td>' + escapeHtml(a.location || '') + '</td><td>' + escapeHtml(a.created_by || '') + '</td><td><span class="pill ' + a.status + '">' + a.status + '</span></td></tr>';
      });
      html += '</tbody></table>';
      document.getElementById('anchorsPanel').innerHTML = html;
    }

    function statusButtons(type, id) {
      return '<button onclick="updateReview(\\'' + type + '\\',\\'' + id + '\\',\\'accepted\\')">确认</button> ' +
             '<button onclick="updateReview(\\'' + type + '\\',\\'' + id + '\\',\\'rejected\\')">拒绝</button> ' +
             '<button onclick="updateReview(\\'' + type + '\\',\\'' + id + '\\',\\'needs_revision\\')">待修订</button>';
    }

    async function updateReview(type, id, status) {
      const res = await fetch('/api/review', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ session_id: sessionId, item_type:type, item_id:id, review_status:status })
      });
      if (!res.ok) { setStatus('更新失败：' + await res.text()); return; }
      currentState = await res.json();
      renderState(currentState);
      setStatus('审查状态已更新：' + status);
    }

    async function exportReviewed() {
      const form = new FormData();
      form.append('session_id', sessionId);
      const res = await fetch('/api/export', { method:'POST', body:form });
      if (!res.ok) { setStatus('导出失败：' + await res.text()); return; }
      const data = await res.json();
      setStatus('已导出：\\n工作底稿：' + data.draft_path + '\\n正式图谱：' + (data.formal_path || '暂无 accepted 节点，未生成正式图谱'));
    }

    function showTab(name) {
      ['nodes','edges','anchors'].forEach(k => {
        document.getElementById(k + 'Panel').classList.toggle('hidden', k !== name);
        document.getElementById(k.slice(0, -1) + 'Tab')?.classList.toggle('active', k === name);
      });
    }
    function escapeHtml(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    function clip(s, n) { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  </script>
</body>
</html>"""
