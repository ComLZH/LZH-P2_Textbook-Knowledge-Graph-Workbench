from __future__ import annotations

from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any

from ..contracts import DraftKnowledgeItemDTO, EvidenceAnchorDTO, FormalEdgeDTO, FormalGraphWorkbookDTO
from ..utils.chapter_layout import build_chapter_layout_plans
from ..utils.graph_semantics import is_container_node_type, normalize_node_type, normalize_relation_type


class ReviewHtmlExporter:
    def export_review(
        self,
        *,
        drafts: list[DraftKnowledgeItemDTO],
        workbook: FormalGraphWorkbookDTO,
        file_path: str | Path,
    ) -> None:
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            self._build_html(drafts=drafts, workbook=workbook),
            encoding="utf-8",
        )

    def _build_html(
        self,
        *,
        drafts: list[DraftKnowledgeItemDTO],
        workbook: FormalGraphWorkbookDTO,
    ) -> str:
        draft_by_node_id = {draft.candidate_node_id: draft for draft in drafts}
        layout = self._build_layout(workbook)
        graph_width = layout["width"]
        graph_height = layout["height"]
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{escape(workbook.metadata.graph_id)} Review</title>
  <style>
    :root {{
      --ink: #1d2430;
      --muted: #687386;
      --surface: #fffdf8;
      --surface-2: #f6f8fb;
      --line: #d7dde8;
      --selected: #e85d04;
      --chapter-bg: #f8f4e7;
      --chapter-line: #c5a14a;
      --knowledge: #324a5f;
      --pending: #ad6b00;
      --accepted: #1d7a3f;
      --rejected: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: #edf1f5;
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    }}
    header {{
      padding: 18px 24px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }}
    h1 {{ margin: 0 0 6px; font-size: 22px; }}
    h2 {{ margin: 0; padding: 12px 14px; font-size: 15px; border-bottom: 1px solid var(--line); }}
    .summary {{ color: var(--muted); line-height: 1.55; }}
    .workbench {{
      display: grid;
      grid-template-columns: minmax(380px, 38vw) minmax(640px, 1fr);
      gap: 14px;
      height: calc(100vh - 88px);
      padding: 14px;
    }}
    .right-stack {{
      display: grid;
      grid-template-rows: minmax(300px, 48%) minmax(320px, 1fr);
      gap: 14px;
      min-width: 0;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      min-width: 0;
      min-height: 0;
    }}
    .panel-body {{ height: calc(100% - 43px); overflow: auto; }}
    .source-body {{
      display: grid;
      grid-template-rows: minmax(220px, 52%) minmax(180px, 1fr);
      height: calc(100% - 43px);
    }}
    .source-preview {{
      background: var(--surface-2);
      border-bottom: 1px solid var(--line);
      overflow: auto;
      padding: 10px;
    }}
    object.source-object, iframe.source-object {{
      width: 100%;
      height: 100%;
      min-height: 360px;
      border: 0;
      background: white;
    }}
    img.source-image {{ max-width: 100%; height: auto; display: block; margin: 0 auto; }}
    .source-placeholder {{ color: var(--muted); line-height: 1.7; padding: 16px; }}
    .anchor-list {{ padding: 10px; overflow: auto; }}
    .anchor {{
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      display: block;
      text-align: left;
      border-radius: 6px;
      padding: 9px 10px;
      margin-bottom: 8px;
      cursor: pointer;
      line-height: 1.45;
    }}
    .anchor.selected, tr.selected, .node.selected rect, .chapter-container.selected rect {{
      outline: 2px solid rgba(232, 93, 4, .55);
      background: rgba(232, 93, 4, .08);
    }}
    .anchor-title {{ font-weight: 700; }}
    .anchor-meta {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .graph-wrap {{ overflow: auto; padding: 10px; height: 100%; }}
    svg {{ min-width: {graph_width}px; min-height: {graph_height}px; }}
    .chapter-container {{ cursor: pointer; }}
    .chapter-frame {{
      fill: var(--chapter-bg);
      stroke: var(--chapter-line);
      stroke-width: 1.8;
    }}
    .chapter-header {{
      fill: #f2e7bf;
      stroke: var(--chapter-line);
      stroke-width: 0.8;
    }}
    .chapter-container.selected .chapter-frame {{
      stroke: var(--selected);
      stroke-width: 4;
    }}
    .chapter-title {{ fill: #594214; font-size: 17px; font-weight: 800; }}
    .chapter-subtitle {{ fill: #7a6226; font-size: 12px; font-weight: 650; }}
    .node {{ cursor: grab; touch-action: none; }}
    .node.dragging {{ cursor: grabbing; }}
    .node rect {{ stroke: rgba(29,36,48,.26); stroke-width: 1.4; }}
    .node.concept rect {{ fill: var(--knowledge); stroke: #0f172a; }}
    .node.property rect {{ fill: #334e68; stroke: #243b53; }}
    .node.rule rect {{ fill: #5b21b6; stroke: #4c1d95; }}
    .node.method rect {{ fill: #166534; stroke: #14532d; }}
    .node.representation rect {{ fill: #d7f5f1; stroke: #0f766e; }}
    .node.problem_type rect {{ fill: #ffedd5; stroke: #c2410c; }}
    .node.application rect {{ fill: #fef3c7; stroke: #d97706; }}
    .node text {{ fill: white; font-size: 13px; font-weight: 700; pointer-events: none; }}
    .node .subtext {{ fill: rgba(255,255,255,.8); font-size: 10px; font-weight: 550; }}
    .node.representation text, .node.problem_type text, .node.application text, .node.structural text {{
      fill: #7c2d12;
    }}
    .node.representation .subtext, .node.problem_type .subtext, .node.application .subtext {{
      fill: rgba(124,45,18,.76);
    }}
    .node.main-path rect {{ stroke-width: 2.2; }}
    .node.main-path text {{ font-size: 14px; }}
    .node.branch rect {{ stroke-width: 1.8; }}
    .node.auxiliary rect {{ opacity: .92; }}
    .node.auxiliary text {{ font-size: 12px; }}
    .node.fallback rect {{ fill: #fff7ed; stroke: #c6901a; }}
    .node.fallback text, .node.fallback .subtext, .node.structural text, .node.structural .subtext {{
      fill: #8a5a00;
    }}
    .node.structural rect {{ fill: #fffaf0; stroke: #c6901a; }}
    .node.selected rect, .node.dragging rect {{ stroke: var(--selected); stroke-width: 4; }}
    .edge {{ stroke: #6b7280; stroke-width: 2; fill: none; opacity: .7; cursor: pointer; }}
    .edge.layout_main_path {{ stroke: #2563eb; stroke-width: 4.2; opacity: .82; }}
    .edge.layout_branch {{ stroke: #64748b; stroke-width: 2.4; opacity: .66; stroke-dasharray: 7 5; }}
    .edge.layout_auxiliary {{ stroke: #b45309; stroke-width: 1.8; opacity: .58; stroke-dasharray: 10 4 2 4; }}
    .edge.prerequisite {{ stroke: #1d4ed8; stroke-width: 3.6; opacity: .98; }}
    .edge.progressive {{ stroke: #0f766e; stroke-width: 3.3; opacity: .96; }}
    .edge.derives_to {{ stroke: #7c3aed; stroke-width: 3.1; opacity: .95; }}
    .edge.explains {{ stroke: #64748b; stroke-dasharray: 6 5; stroke-width: 1.6; opacity: .6; }}
    .edge.equivalent {{ stroke: #475467; stroke-width: 1.9; opacity: .68; }}
    .edge.parallel {{ stroke: #7c3aed; stroke-dasharray: 8 6; stroke-width: 1.6; opacity: .58; }}
    .edge.contrast {{ stroke: #b42318; stroke-dasharray: 8 6; stroke-width: 1.8; opacity: .64; }}
    .edge.applies_to {{ stroke: #b45309; stroke-width: 2.1; opacity: .72; }}
    .edge.represented_by {{ stroke: #0f766e; stroke-dasharray: 10 4 2 4; stroke-width: 1.5; opacity: .58; }}
    .edge.selected {{ stroke: var(--selected); stroke-width: 4.5; opacity: 1; }}
    .edge-label-bg {{ fill: #fffdf8; stroke: #cbd5e1; stroke-width: .8; opacity: .92; }}
    .edge-label {{ font-size: 12px; font-weight: 700; fill: #344054; text-anchor: middle; paint-order: stroke; stroke: #fffdf8; stroke-width: 3px; }}
    .contains-highlight {{ fill: none; stroke: transparent; stroke-width: 4; pointer-events: none; }}
    .contains-highlight.selected {{ stroke: var(--selected); }}
    .tables {{
      display: grid;
      grid-template-rows: auto auto 1fr;
      height: 100%;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 8px 9px; border-bottom: 1px solid rgba(29,36,48,.09); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; background: #f9fafb; position: sticky; top: 0; }}
    tr {{ cursor: pointer; }}
    tr.selected {{ background: rgba(232, 93, 4, .12); }}
    .pill {{ display: inline-block; padding: 2px 7px; border-radius: 999px; background: #e8edf4; color: #334155; font-size: 12px; }}
    .pill.accepted {{ background: #dcfce7; color: var(--accepted); }}
    .pill.pending {{ background: #fef3c7; color: var(--pending); }}
    .pill.rejected {{ background: #fee4e2; color: var(--rejected); }}
    .reason {{ color: var(--muted); max-width: 360px; }}
    .evidence-panel {{ padding: 12px 14px; line-height: 1.6; color: #374151; }}
    .evidence-panel p {{ margin: 0 0 12px; }}
    @media (max-width: 1100px) {{
      .workbench {{ grid-template-columns: 1fr; height: auto; }}
      .right-stack {{ grid-template-rows: auto auto; }}
      .panel {{ min-height: 320px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>教材原文驱动审查工作台</h1>
    <div class="summary">
      图谱：{escape(workbook.metadata.graph_id)}；节点：{len(workbook.nodes)}；关系：{len(workbook.edges)}。
      大模型或结构化抽取结果在此只作为候选底稿，教师确认后才进入正式图谱。
    </div>
  </header>
  <main class="workbench">
    <section class="panel">
      <h2>教材原文与证据锚点</h2>
      <div class="source-body">
        <div class="source-preview">{self._build_source_preview(drafts)}</div>
        <div class="anchor-list">{self._build_anchor_list(drafts)}</div>
      </div>
    </section>
    <section class="right-stack">
      <div class="panel">
        <h2>知识图谱概览</h2>
        <div class="graph-wrap">{self._build_svg(workbook, layout)}</div>
      </div>
      <div class="panel">
        <h2>知识点与关系结构表</h2>
        <div class="panel-body tables">
          {self._build_node_table(workbook, draft_by_node_id)}
          {self._build_edge_table(workbook)}
          {self._build_evidence_panel(drafts)}
        </div>
      </div>
    </section>
  </main>
  <script>
    function clearSelection() {{
      document.querySelectorAll('.selected').forEach(function(el) {{
        el.classList.remove('selected');
      }});
    }}
    function markSelected(selector) {{
      document.querySelectorAll(selector).forEach(function(el) {{
        el.classList.add('selected');
        if (el.scrollIntoView) {{
          el.scrollIntoView({{ block: 'nearest', inline: 'nearest' }});
        }}
      }});
    }}
    function selectNode(id) {{
      clearSelection();
      markSelected('[data-node-id="' + id + '"]');
      markSelected('[data-target-id="' + id + '"]');
    }}
    function selectEdge(id) {{
      clearSelection();
      markSelected('[data-edge-id="' + id + '"]');
    }}
    function selectAnchor(id, targetId) {{
      clearSelection();
      markSelected('[data-anchor-id="' + id + '"]');
      if (targetId) {{
        markSelected('[data-node-id="' + targetId + '"]');
      }}
    }}
    function svgPoint(svg, event) {{
      var point = svg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      return point.matrixTransform(svg.getScreenCTM().inverse());
    }}
    function nodeGeometry(id) {{
      var node = document.querySelector('.node[data-node-id="' + id + '"]');
      if (!node) {{ return null; }}
      return {{
        x: Number(node.dataset.x || 0),
        y: Number(node.dataset.y || 0),
        width: Number(node.dataset.width || 0),
        height: Number(node.dataset.height || 0)
      }};
    }}
    function edgePath(source, target) {{
      var sx = source.x + source.width / 2;
      var sy = source.y + source.height / 2;
      var tx = target.x + target.width / 2;
      var ty = target.y + target.height / 2;
      var horizontal = Math.abs(tx - sx) >= Math.abs(ty - sy);
      var startX, startY, endX, endY, delta;
      if (horizontal) {{
        startX = tx >= sx ? source.x + source.width : source.x;
        startY = sy;
        endX = tx >= sx ? target.x : target.x + target.width;
        endY = ty;
        delta = Math.max(42, Math.abs(endX - startX) * 0.35);
        return 'M ' + startX + ' ' + startY + ' C ' + (startX + delta) + ' ' + startY + ', ' + (endX - delta) + ' ' + endY + ', ' + endX + ' ' + endY;
      }}
      startX = sx;
      startY = ty >= sy ? source.y + source.height : source.y;
      endX = tx;
      endY = ty >= sy ? target.y : target.y + target.height;
      delta = Math.max(34, Math.abs(endY - startY) * 0.35);
      return 'M ' + startX + ' ' + startY + ' C ' + startX + ' ' + (startY + delta) + ', ' + endX + ' ' + (endY - delta) + ', ' + endX + ' ' + endY;
    }}
    function refreshConnectedEdges(nodeId) {{
      document.querySelectorAll('[data-source-id="' + nodeId + '"], [data-target-id="' + nodeId + '"]').forEach(function(edgeGroup) {{
        var source = nodeGeometry(edgeGroup.dataset.sourceId);
        var target = nodeGeometry(edgeGroup.dataset.targetId);
        if (!source || !target) {{ return; }}
        var path = edgeGroup.querySelector('path.edge');
        if (path) {{ path.setAttribute('d', edgePath(source, target)); }}
        var label = edgeGroup.querySelector('.edge-label');
        if (label) {{
          var labelX = (source.x + source.width / 2 + target.x + target.width / 2) / 2;
          var labelY = (source.y + source.height / 2 + target.y + target.height / 2) / 2 - Number(edgeGroup.dataset.labelOffset || 14);
          label.setAttribute('x', String(labelX));
          label.setAttribute('y', String(labelY));
          var bg = edgeGroup.querySelector('.edge-label-bg');
          if (bg) {{
            bg.setAttribute('x', String(labelX - Number(bg.getAttribute('width') || 0) / 2));
            bg.setAttribute('y', String(labelY - 15));
          }}
        }}
      }});
    }}
    document.querySelectorAll('svg .node').forEach(function(node) {{
      var active = null;
      node.addEventListener('pointerdown', function(event) {{
        active = {{
          pointerId: event.pointerId,
          start: svgPoint(node.ownerSVGElement, event),
          x: Number(node.dataset.x || 0),
          y: Number(node.dataset.y || 0)
        }};
        node.classList.add('dragging');
        node.setPointerCapture(event.pointerId);
      }});
      node.addEventListener('pointermove', function(event) {{
        if (!active || active.pointerId !== event.pointerId) {{ return; }}
        var current = svgPoint(node.ownerSVGElement, event);
        var nextX = active.x + current.x - active.start.x;
        var nextY = active.y + current.y - active.start.y;
        node.dataset.x = String(nextX);
        node.dataset.y = String(nextY);
        node.setAttribute('transform', 'translate(' + nextX + ',' + nextY + ')');
        refreshConnectedEdges(node.dataset.nodeId);
      }});
      node.addEventListener('pointerup', function(event) {{
        if (!active || active.pointerId !== event.pointerId) {{ return; }}
        node.classList.remove('dragging');
        active = null;
      }});
      node.addEventListener('pointercancel', function() {{
        node.classList.remove('dragging');
        active = null;
      }});
    }});
    document.querySelectorAll('[data-node-id]').forEach(function(el) {{
      el.addEventListener('click', function() {{ selectNode(el.dataset.nodeId); }});
    }});
    document.querySelectorAll('[data-edge-id]').forEach(function(el) {{
      el.addEventListener('click', function() {{ selectEdge(el.dataset.edgeId); }});
    }});
    document.querySelectorAll('[data-anchor-id]').forEach(function(el) {{
      el.addEventListener('click', function() {{
        selectAnchor(el.dataset.anchorId, el.dataset.targetId || '');
      }});
    }});
  </script>
</body>
</html>
"""

    def _build_source_preview(self, drafts: list[DraftKnowledgeItemDTO]) -> str:
        source = self._first_source(drafts)
        if source is None:
            return '<div class="source-placeholder">暂无教材原文来源。可先通过结构化底稿与证据文本进行审查。</div>'
        source_id, source_path, source_format = source
        path = Path(source_path) if source_path else None
        if path is not None and path.exists():
            uri = path.resolve().as_uri()
            if source_format == "pdf":
                return (
                    f'<object class="source-object" data="{escape(uri)}" '
                    'type="application/pdf">'
                    f'<div class="source-placeholder">PDF 预览不可用：{escape(source_path)}</div>'
                    "</object>"
                )
            if source_format in {"png", "jpg", "jpeg"}:
                return f'<img class="source-image" src="{escape(uri)}" alt="{escape(source_id)}">'
        return (
            '<div class="source-placeholder">'
            f"来源：{escape(source_id)}<br>格式：{escape(source_format or 'structured_json')}<br>"
            f"路径：{escape(source_path or '未记录可预览文件')}<br>"
            "当前静态工作台会先展示证据文本；PDF/图片存在本地路径时会自动预览。"
            "</div>"
        )

    @staticmethod
    def _first_source(
        drafts: list[DraftKnowledgeItemDTO],
    ) -> tuple[str, str, str] | None:
        for draft in drafts:
            if draft.source_id or draft.source_path or draft.source_format:
                return (draft.source_id, draft.source_path, draft.source_format)
        return None

    def _build_anchor_list(self, drafts: list[DraftKnowledgeItemDTO]) -> str:
        buttons = []
        for draft in drafts:
            anchors = draft.evidence_anchors or [self._fallback_anchor(draft)]
            for anchor in anchors:
                target_id = anchor.target_ids[0] if anchor.target_ids else draft.candidate_node_id
                title = anchor.anchor_text or draft.source_text or draft.candidate_display_name
                buttons.append(
                    '<button class="anchor" data-anchor-id="{anchor_id}" data-target-id="{target_id}">'
                    '<div class="anchor-title">{name}</div>'
                    '<div>{text}</div>'
                    '<div class="anchor-meta">{location} / {created_by} / {status}</div>'
                    '</button>'.format(
                        anchor_id=escape(anchor.anchor_id or f"{draft.draft_id}:anchor"),
                        target_id=escape(target_id),
                        name=escape(draft.candidate_display_name),
                        text=escape(self._clip(title, 96)),
                        location=escape(anchor.source_location or draft.source_location or "unknown"),
                        created_by=escape(anchor.created_by or draft.extractor_source),
                        status=escape(anchor.review_status or draft.review_status),
                    )
                )
        return "".join(buttons) or '<div class="source-placeholder">暂无证据锚点。</div>'

    @staticmethod
    def _fallback_anchor(draft: DraftKnowledgeItemDTO) -> EvidenceAnchorDTO:
        return EvidenceAnchorDTO(
            anchor_id=f"{draft.draft_id}:anchor",
            source_id=draft.source_id,
            source_path=draft.source_path,
            source_format=draft.source_format,
            source_document_type=draft.source_document_type,
            source_location=draft.source_location,
            anchor_text=draft.source_text,
            target_ids=[draft.candidate_node_id],
            confidence=draft.confidence,
            created_by=draft.extractor_source,
            review_status=draft.review_status,
        )

    def _build_layout(self, workbook: FormalGraphWorkbookDTO) -> dict[str, Any]:
        node_by_id = {node.node_id: node for node in workbook.nodes}
        children_by_parent: dict[str, list[str]] = defaultdict(list)
        layout_plans = build_chapter_layout_plans(workbook.nodes, workbook.edges)
        node_roles = self._node_role_map(workbook, layout_plans)
        chapter_nodes = [
            node
            for node in workbook.nodes
            if is_container_node_type(node.node_type or node.knowledge_type)
        ]
        for node in workbook.nodes:
            if node.parent_node_id:
                children_by_parent[node.parent_node_id].append(node.node_id)

        if not chapter_nodes:
            chapter_nodes = [node for node in workbook.nodes if not node.parent_node_id]

        positions: dict[str, tuple[int, int]] = {}
        containers: list[dict[str, Any]] = []
        max_width = 920
        current_y = 50
        for chapter in chapter_nodes:
            child_ids = children_by_parent.get(chapter.node_id, [])
            plan = layout_plans.get(chapter.node_id)
            structural_children = list(plan.structural_children if plan else [])
            main_path = list(plan.main_path_nodes if plan else [])
            branch_groups = dict(plan.branch_groups if plan else {})
            auxiliary_attachments = dict(plan.auxiliary_attachments if plan else {})
            assigned_child_ids = set(structural_children)
            assigned_child_ids.update(main_path)
            assigned_child_ids.update(item for values in branch_groups.values() for item in values)
            assigned_child_ids.update(item for values in auxiliary_attachments.values() for item in values)
            fallback_children = [child_id for child_id in child_ids if child_id not in assigned_child_ids]
            structural_width = self._lane_width(structural_children, node_roles, gap=16)
            main_width = self._lane_width(main_path, node_roles, gap=24)
            fallback_width = self._lane_width(fallback_children, node_roles, gap=16)
            aux_width = max(
                (self._lane_width(values, node_roles, gap=16) for values in auxiliary_attachments.values()),
                default=0,
            )
            container_width = int(max(700, 140 + max(structural_width, main_width, fallback_width, aux_width, 280)))
            structural_lane = 72 if structural_children else 0
            main_lane = 96 if main_path else 0
            max_branch_depth = max((len(values) for values in branch_groups.values()), default=0)
            branch_lane = 82 * max(1, max_branch_depth) if branch_groups else 0
            auxiliary_lane = 74 if auxiliary_attachments else 0
            fallback_lane = 72 if fallback_children else 0
            container_height = 118 + structural_lane + main_lane + branch_lane + auxiliary_lane + fallback_lane + 18
            x = 42
            y = current_y
            positions[chapter.node_id] = (x, y, container_width, container_height)
            content_left = x + 34
            content_right = x + container_width - 34
            lane_y = y + 86
            if structural_children:
                x_cursor = self._centered_lane_start(content_left, content_right, structural_children, node_roles, gap=16)
                for child_id in structural_children:
                    width_box, height_box = self._node_box_for_role(node_roles.get(child_id, "structural"))
                    positions[child_id] = (x_cursor, lane_y, width_box, height_box)
                    x_cursor += width_box + 16
                lane_y += 90
            main_centers: dict[str, int] = {}
            if main_path:
                x_cursor = self._centered_lane_start(content_left, content_right, main_path, node_roles, gap=24)
                for child_id in main_path:
                    width_box, height_box = self._node_box_for_role(node_roles.get(child_id, "main_path"))
                    positions[child_id] = (x_cursor, lane_y, width_box, height_box)
                    main_centers[child_id] = x_cursor + width_box // 2
                    x_cursor += width_box + 24
                lane_y += 92
            if branch_groups:
                for parent_id, branch_ids in branch_groups.items():
                    center_x = main_centers.get(parent_id, content_left + 90)
                    total_width = self._lane_width(branch_ids, node_roles, gap=18)
                    x_cursor = int(max(content_left, min(content_right - total_width, center_x - total_width / 2)))
                    for branch_index, branch_id in enumerate(branch_ids):
                        width_box, height_box = self._node_box_for_role(node_roles.get(branch_id, "branch"))
                        positions[branch_id] = (x_cursor, lane_y + branch_index * 82, width_box, height_box)
                        x_cursor += width_box + 18
                lane_y += max(1, max_branch_depth) * 84
            if auxiliary_attachments:
                for parent_id, attachment_ids in auxiliary_attachments.items():
                    if not attachment_ids:
                        continue
                    total_width = self._lane_width(attachment_ids, node_roles, gap=16)
                    parent_center = main_centers.get(parent_id, (content_left + content_right) // 2)
                    x_cursor = int(max(content_left, min(content_right - total_width, parent_center - total_width / 2)))
                    for attachment_id in attachment_ids:
                        width_box, height_box = self._node_box_for_role(node_roles.get(attachment_id, "auxiliary"))
                        positions[attachment_id] = (x_cursor, lane_y, width_box, height_box)
                        x_cursor += width_box + 16
                lane_y += 84
            if fallback_children:
                x_cursor = self._centered_lane_start(content_left, content_right, fallback_children, node_roles, gap=16)
                for child_id in fallback_children:
                    width_box, height_box = self._node_box_for_role(node_roles.get(child_id, "fallback"))
                    positions[child_id] = (x_cursor, lane_y, width_box, height_box)
                    x_cursor += width_box + 16
            containers.append(
                {
                    "node": chapter,
                    "plan": plan,
                    "child_ids": child_ids,
                    "x": x,
                    "y": y,
                    "width": container_width,
                    "height": container_height,
                }
            )
            max_width = max(max_width, container_width + 100)
            current_y += container_height + 70

        orphan_nodes = [node for node in workbook.nodes if node.node_id not in positions]
        if orphan_nodes:
            y = current_y + 30
            for index, node in enumerate(orphan_nodes):
                width_box, height_box = self._node_box_for_role(node_roles.get(node.node_id, "orphan"))
                positions[node.node_id] = (80 + index * (width_box + 24), y, width_box, height_box)
            max_width = max(max_width, 180 + len(orphan_nodes) * 210)
            current_y = y + 150

        return {
            "positions": positions,
            "containers": containers,
            "width": max_width,
            "height": max(500, current_y + 40),
            "node_by_id": node_by_id,
            "node_roles": node_roles,
            "layout_plans": layout_plans,
        }

    def _build_svg(self, workbook: FormalGraphWorkbookDTO, layout: dict[str, Any]) -> str:
        positions = layout["positions"]
        width = layout["width"]
        height = layout["height"]
        node_roles = layout["node_roles"]
        layout_plans = layout["layout_plans"]
        formal_relation_pairs = self._formal_relation_pairs(workbook.edges)
        containers = "\n".join(
            self._chapter_container_svg(container) for container in layout["containers"]
        )
        guide_edges = "\n".join(
            self._guide_edge_svg(source_id, target_id, relation_type, positions)
            for source_id, target_id, relation_type in self._layout_guide_edges(
                layout_plans,
                suppressed_pairs=formal_relation_pairs,
            )
            if source_id in positions and target_id in positions
        )
        edges = "\n".join(
            self._edge_svg(edge, positions)
            for edge in workbook.edges
            if edge.relation_type != "contains" and self._edge_visible(edge, positions)
        )
        nodes = "\n".join(
            self._node_svg(node, positions[node.node_id], node_roles.get(node.node_id, "fallback"))
            for node in workbook.nodes
            if node.node_id in positions and not self._is_chapter_node(node)
        )
        contains_highlights = "\n".join(
            self._contains_highlight_svg(edge, positions)
            for edge in workbook.edges
            if edge.relation_type == "contains" and self._edge_visible(edge, positions)
        )
        return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="knowledge graph review">
  <defs>
    {self._marker_def("arrow-blue", "#1d4ed8")}
    {self._marker_def("arrow-green", "#0f766e")}
    {self._marker_def("arrow-purple", "#7c3aed")}
    {self._marker_def("arrow-orange", "#b45309")}
    {self._marker_def("arrow-gray", "#64748b")}
    {self._marker_def("arrow-red", "#b42318")}
  </defs>
  {containers}
  {guide_edges}
  {edges}
  {nodes}
  {contains_highlights}
</svg>"""

    @staticmethod
    def _is_chapter_node(node: object) -> bool:
        return is_container_node_type(node.node_type or node.knowledge_type)

    @staticmethod
    def _chapter_container_svg(container: dict[str, Any]) -> str:
        node = container["node"]
        plan = container.get("plan")
        node_id = escape(node.node_id)
        title = escape(node.display_name)
        detail = escape(
            f"{ReviewHtmlExporter._layout_mode_label(plan.layout_mode if plan else '')} / 主线 {len(plan.main_path_nodes) if plan else 0} / 置信度 {int((plan.local_confidence if plan else 0) * 100)}%"
        )
        return f"""<g class="chapter-container" data-node-id="{node_id}">
    <rect class="chapter-frame" x="{container['x']}" y="{container['y']}" width="{container['width']}" height="{container['height']}" rx="8"></rect>
    <rect class="chapter-header" x="{container['x']}" y="{container['y']}" width="{container['width']}" height="58" rx="8"></rect>
    <text class="chapter-title" x="{container['x'] + 22}" y="{container['y'] + 32}">{title}</text>
    <text class="chapter-subtitle" x="{container['x'] + 22}" y="{container['y'] + 54}">{detail}</text>
  </g>"""

    @staticmethod
    def _edge_visible(edge: FormalEdgeDTO, positions: dict[str, tuple[int, int]]) -> bool:
        return edge.source_node_id in positions and edge.target_node_id in positions

    @staticmethod
    def _edge_id(edge: FormalEdgeDTO) -> str:
        return f"{edge.source_node_id}--{edge.relation_type}--{edge.target_node_id}"

    @staticmethod
    def _layout_guide_edges(
        layout_plans: dict[str, Any],
        suppressed_pairs: set[frozenset[str]] | None = None,
    ) -> list[tuple[str, str, str]]:
        suppressed_pairs = suppressed_pairs or set()
        guide_edges: list[tuple[str, str, str]] = []
        for plan in layout_plans.values():
            main_path = list(plan.main_path_nodes)
            for index in range(len(main_path) - 1):
                source_id = main_path[index]
                target_id = main_path[index + 1]
                if frozenset({source_id, target_id}) not in suppressed_pairs:
                    guide_edges.append((source_id, target_id, "layout_main_path"))
            for parent_id, child_ids in plan.branch_groups.items():
                for child_id in child_ids:
                    if frozenset({parent_id, child_id}) not in suppressed_pairs:
                        guide_edges.append((parent_id, child_id, "layout_branch"))
            for parent_id, child_ids in plan.auxiliary_attachments.items():
                for child_id in child_ids:
                    if frozenset({parent_id, child_id}) not in suppressed_pairs:
                        guide_edges.append((parent_id, child_id, "layout_auxiliary"))
        return guide_edges

    @staticmethod
    def _formal_relation_pairs(edges: list[FormalEdgeDTO]) -> set[frozenset[str]]:
        return {
            frozenset({edge.source_node_id, edge.target_node_id})
            for edge in edges
            if normalize_relation_type(edge.relation_type) != "contains"
        }

    def _guide_edge_svg(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        positions: dict[str, tuple[int, int, int, int]],
    ) -> str:
        edge = FormalEdgeDTO(
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type=relation_type,
            relation_source="layout_plan",
        )
        return self._edge_svg(edge, positions)

    def _edge_svg(self, edge: FormalEdgeDTO, positions: dict[str, tuple[int, int]]) -> str:
        source_x, source_y, source_w, source_h = positions[edge.source_node_id]
        target_x, target_y, target_w, target_h = positions[edge.target_node_id]
        source_center_x = source_x + source_w / 2
        source_center_y = source_y + source_h / 2
        target_center_x = target_x + target_w / 2
        target_center_y = target_y + target_h / 2
        horizontal = abs(target_center_x - source_center_x) >= abs(target_center_y - source_center_y)
        if horizontal:
            start_x = source_x + source_w if target_center_x >= source_center_x else source_x
            start_y = source_center_y
            end_x = target_x if target_center_x >= source_center_x else target_x + target_w
            end_y = target_center_y
            control_delta = max(42, abs(end_x - start_x) * 0.35)
            path_d = (
                f"M {start_x} {start_y} "
                f"C {start_x + control_delta} {start_y}, {end_x - control_delta} {end_y}, {end_x} {end_y}"
            )
        else:
            start_x = source_center_x
            start_y = source_y + source_h if target_center_y >= source_center_y else source_y
            end_x = target_center_x
            end_y = target_y if target_center_y >= source_center_y else target_y + target_h
            control_delta = max(34, abs(end_y - start_y) * 0.35)
            path_d = (
                f"M {start_x} {start_y} "
                f"C {start_x} {start_y + control_delta}, {end_x} {end_y - control_delta}, {end_x} {end_y}"
            )
        edge_id = escape(self._edge_id(edge))
        relation_type = escape(normalize_relation_type(edge.relation_type, default=str(edge.relation_type)))
        marker = self._marker_id_for_relation(relation_type)
        label_svg = ""
        if self._edge_label_visible(relation_type):
            mid_x = (start_x + end_x) / 2
            mid_y = (start_y + end_y) / 2 - self._relation_label_offset(relation_type)
            label = escape(self._relation_label(relation_type))
            label_width = max(36, len(label) * 14)
            label_svg = (
                f'\n    <rect class="edge-label-bg" x="{mid_x - label_width / 2 - 6}" y="{mid_y - 15}" '
                f'width="{label_width + 12}" height="22" rx="5"></rect>'
                f'\n    <text class="edge-label" x="{mid_x}" y="{mid_y}">{label}</text>'
            )
        source_id = escape(edge.source_node_id)
        target_id = escape(edge.target_node_id)
        label_offset = self._relation_label_offset(relation_type)
        return f"""<g data-edge-id="{edge_id}" data-source-id="{source_id}" data-target-id="{target_id}" data-label-offset="{label_offset}">
    <path class="edge {relation_type}" d="{path_d}" marker-end="url(#{marker})"></path>{label_svg}
  </g>"""

    def _contains_highlight_svg(
        self,
        edge: FormalEdgeDTO,
        positions: dict[str, tuple[int, int]],
    ) -> str:
        x, y, width, height = positions[edge.target_node_id]
        return (
            f'<rect class="contains-highlight" data-edge-id="{escape(self._edge_id(edge))}" '
            f'x="{x - 6}" y="{y - 6}" width="{width + 12}" height="{height + 12}" rx="8"></rect>'
        )

    @staticmethod
    def _node_svg(node: object, position: tuple[int, int, int, int], role: str) -> str:
        x, y, width, height = position
        node_id = escape(node.node_id)
        label = escape(ReviewHtmlExporter._clip(node.display_name, 24 if role == "main_path" else 18))
        normalized_type = normalize_node_type(node.node_type or node.knowledge_type)
        detail = escape(f"{normalized_type} / {ReviewHtmlExporter._role_label(role)}")
        node_class = escape((normalized_type or "concept") + " " + role.replace("_", "-"))
        return f"""<g class="node {node_class}" data-node-id="{node_id}" data-x="{x}" data-y="{y}" data-width="{width}" data-height="{height}" transform="translate({x},{y})">
    <rect width="{width}" height="{height}" rx="8"></rect>
    <text x="14" y="29">{label}</text>
    <text class="subtext" x="14" y="{height - 12}">{detail}</text>
  </g>"""

    def _node_role_map(
        self,
        workbook: FormalGraphWorkbookDTO,
        layout_plans: dict[str, Any],
    ) -> dict[str, str]:
        roles: dict[str, str] = {}
        for container_id, plan in layout_plans.items():
            roles[container_id] = "container"
            for child_id in plan.structural_children:
                roles[child_id] = "structural"
            for child_id in plan.main_path_nodes:
                roles[child_id] = "main_path"
            for child_ids in plan.branch_groups.values():
                for child_id in child_ids:
                    roles.setdefault(child_id, "branch")
            for child_ids in plan.auxiliary_attachments.values():
                for child_id in child_ids:
                    roles.setdefault(child_id, "auxiliary")
            for child_id in plan.unassigned_nodes:
                roles.setdefault(child_id, "fallback")
        for node in workbook.nodes:
            if node.node_id in roles:
                continue
            if is_container_node_type(node.node_type or node.knowledge_type):
                roles[node.node_id] = "container"
            elif node.parent_node_id:
                roles[node.node_id] = "fallback"
            else:
                roles[node.node_id] = "orphan"
        return roles

    @staticmethod
    def _node_box_for_role(role: str) -> tuple[int, int]:
        if role == "main_path":
            return (198, 82)
        if role == "branch":
            return (172, 68)
        if role == "auxiliary":
            return (156, 58)
        if role == "structural":
            return (150, 46)
        if role == "orphan":
            return (170, 62)
        return (158, 58)

    def _lane_width(self, node_ids: list[str], node_roles: dict[str, str], *, gap: int = 24) -> int:
        if not node_ids:
            return 0
        total = 0
        for index, node_id in enumerate(node_ids):
            total += self._node_box_for_role(node_roles.get(node_id, "fallback"))[0]
            if index < len(node_ids) - 1:
                total += gap
        return total

    def _centered_lane_start(
        self,
        content_left: int,
        content_right: int,
        node_ids: list[str],
        node_roles: dict[str, str],
        *,
        gap: int = 24,
    ) -> int:
        total_width = self._lane_width(node_ids, node_roles, gap=gap)
        return int(max(content_left, content_left + (content_right - content_left - total_width) / 2))

    @staticmethod
    def _layout_mode_label(mode: str) -> str:
        labels = {
            "single_path": "单主线",
            "main_path_with_branches": "主线+分支",
            "multi_core_clusters": "多核心",
        }
        return labels.get(mode, "章节结构")

    @staticmethod
    def _role_label(role: str) -> str:
        labels = {
            "main_path": "主路径",
            "branch": "分支",
            "auxiliary": "侧挂",
            "structural": "子结构",
            "fallback": "待确认",
            "orphan": "独立",
        }
        return labels.get(role, "节点")

    @staticmethod
    def _marker_def(marker_id: str, color: str) -> str:
        return (
            f'<marker id="{marker_id}" markerWidth="10" markerHeight="10" refX="9" refY="3" '
            'orient="auto" markerUnits="strokeWidth">'
            f'<path d="M0,0 L0,6 L9,3 z" fill="{color}"></path></marker>'
        )

    @staticmethod
    def _marker_id_for_relation(relation_type: str) -> str:
        if relation_type == "prerequisite":
            return "arrow-blue"
        if relation_type == "layout_main_path":
            return "arrow-blue"
        if relation_type == "layout_branch":
            return "arrow-gray"
        if relation_type == "layout_auxiliary":
            return "arrow-orange"
        if relation_type in {"progressive", "represented_by"}:
            return "arrow-green"
        if relation_type in {"derives_to", "parallel", "equivalent"}:
            return "arrow-purple"
        if relation_type == "applies_to":
            return "arrow-orange"
        if relation_type == "contrast":
            return "arrow-red"
        return "arrow-gray"

    @staticmethod
    def _edge_label_visible(relation_type: str) -> bool:
        return relation_type != "contains"

    @staticmethod
    def _relation_label(relation_type: str) -> str:
        labels = {
            "layout_main_path": "主路径",
            "layout_branch": "分支",
            "layout_auxiliary": "侧挂",
            "prerequisite": "前置",
            "progressive": "递进",
            "derives_to": "推导",
            "explains": "解释",
            "equivalent": "等价",
            "parallel": "并列",
            "contrast": "对比",
            "applies_to": "应用",
            "represented_by": "表征",
        }
        return labels.get(relation_type, relation_type)

    @staticmethod
    def _relation_label_offset(relation_type: str) -> int:
        if relation_type.startswith("layout_"):
            return 28
        if relation_type in {"prerequisite", "progressive", "derives_to"}:
            return 8
        return 14

    @staticmethod
    def _build_node_table(
        workbook: FormalGraphWorkbookDTO,
        draft_by_node_id: dict[str, DraftKnowledgeItemDTO],
    ) -> str:
        rows = []
        for node in workbook.nodes:
            draft = draft_by_node_id.get(node.node_id)
            status = draft.review_status if draft else "accepted"
            reason = draft.reasoning_summary if draft else ""
            rows.append(
                "<tr data-node-id=\"{node_id}\">"
                "<td>{name}</td><td>{kind}</td><td>{chapter}</td>"
                "<td><span class=\"pill {status}\">{status}</span></td><td class=\"reason\">{reason}</td>"
                "</tr>".format(
                    node_id=escape(node.node_id),
                    name=escape(node.display_name),
                    kind=escape(normalize_node_type(node.node_type or node.knowledge_type)),
                    chapter=escape(node.chapter),
                    status=escape(status),
                    reason=escape(ReviewHtmlExporter._clip(reason, 96)),
                )
            )
        return (
            "<table><thead><tr><th>节点</th><th>类型</th><th>章节</th><th>审查</th><th>候选理由</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

    def _build_edge_table(self, workbook: FormalGraphWorkbookDTO) -> str:
        rows = []
        node_names = {node.node_id: node.display_name for node in workbook.nodes}
        for edge in workbook.edges:
            edge_id = self._edge_id(edge)
            rows.append(
                "<tr data-edge-id=\"{edge_id}\">"
                "<td>{source}</td><td>{relation}</td><td>{target}</td>"
                "<td><span class=\"pill {status}\">{status}</span></td><td class=\"reason\">{evidence}</td>"
                "</tr>".format(
                    edge_id=escape(edge_id),
                    source=escape(node_names.get(edge.source_node_id, edge.source_node_id)),
                    relation=escape(normalize_relation_type(edge.relation_type)),
                    target=escape(node_names.get(edge.target_node_id, edge.target_node_id)),
                    status=escape(edge.review_status),
                    evidence=escape(self._clip(edge.relation_evidence, 96)),
                )
            )
        return (
            "<table><thead><tr><th>起点</th><th>关系</th><th>终点</th><th>审查</th><th>证据</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

    @staticmethod
    def _build_evidence_panel(drafts: list[DraftKnowledgeItemDTO]) -> str:
        items = []
        for draft in drafts:
            target_id = escape(draft.candidate_node_id)
            items.append(
                "<p data-node-id=\"{node_id}\"><strong>{name}</strong><br>"
                "位置：{location}<br>来源：{source}<br>证据：{text}</p>".format(
                    node_id=target_id,
                    name=escape(draft.candidate_display_name),
                    location=escape(draft.source_location or "unknown"),
                    source=escape(draft.extractor_source or "unknown"),
                    text=escape(ReviewHtmlExporter._clip(draft.source_text or "No source text recorded.", 180)),
                )
            )
        return f"<div class=\"evidence-panel\">{''.join(items)}</div>"

    @staticmethod
    def _clip(value: object, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "..."
