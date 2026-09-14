from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


class G6ReviewHtmlRenderer:
    def render_to_file(
        self,
        *,
        payload: dict[str, Any],
        output_path: str | Path,
    ) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(payload=payload), encoding="utf-8")

    def render(self, *, payload: dict[str, Any]) -> str:
        graph_id = escape(str(payload.get("graph_id") or "Graph Review"))
        payload_json = (
            json.dumps(payload, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>{graph_id}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      color: #18212f;
      background: #eef2f6;
    }}
    .page {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      height: 100vh;
      min-height: 620px;
    }}
    .main {{
      display: grid;
      grid-template-rows: auto auto 1fr;
      min-width: 0;
      min-height: 0;
    }}
    header {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      padding: 10px 12px;
      background: #fff;
      border-bottom: 1px solid #d8dee9;
    }}
    h1 {{
      margin: 0;
      font-size: 16px;
      white-space: nowrap;
    }}
    button {{
      border: 1px solid #cbd5e1;
      background: #fff;
      border-radius: 6px;
      padding: 6px 9px;
      cursor: pointer;
      font: inherit;
      font-size: 13px;
    }}
    button.active {{
      background: #e8f1ff;
      border-color: #8cb7f8;
      color: #0f4fb4;
    }}
    #graph {{
      min-width: 0;
      min-height: 0;
      background:
        linear-gradient(#eef3f8 1px, transparent 1px),
        linear-gradient(90deg, #eef3f8 1px, transparent 1px),
        #fbfcfe;
      background-size: 28px 28px;
    }}
    .detail {{
      min-width: 0;
      min-height: 0;
      border-left: 1px solid #d8dee9;
      background: #fff;
      padding: 14px;
      overflow: auto;
      font-size: 13px;
      line-height: 1.6;
    }}
    .detail h2 {{
      margin: 0 0 10px;
      font-size: 15px;
    }}
    .kv {{
      color: #64748b;
    }}
    .status {{
      margin-left: auto;
      color: #64748b;
      font-size: 12px;
    }}
    .legend {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      padding: 7px 12px;
      background: #f8fafc;
      border-bottom: 1px solid #e2e8f0;
      color: #475569;
      font-size: 12px;
    }}
    .legend-item {{ display: inline-flex; align-items: center; gap: 5px; }}
    .legend-line {{ width: 24px; height: 0; border-top: 3px solid; }}
    .legend-line.prerequisite {{ border-color: #1d4ed8; }}
    .legend-line.progressive {{ border-color: #0f766e; }}
    .legend-line.auxiliary {{ border-color: #94a3b8; border-top-style: dashed; }}
    .legend-line.symmetric {{ border-color: #7c3aed; border-top-style: double; border-top-width: 4px; }}
    .legend-line.cycle {{ border-color: #b42318; border-top-style: dotted; }}
    .legend-state {{ border: 1px solid; border-radius: 9px; padding: 1px 7px; }}
    .legend-state.pending {{ color: #9a3412; border-color: #fdba74; background: #fff7ed; }}
    .legend-state.revision {{ color: #be123c; border-color: #fda4af; background: #fff1f2; }}
    .load-error {{
      display: none;
      color: #991b1b;
      margin-left: auto;
      font-size: 13px;
    }}
    @media (max-width: 980px) {{
      .page {{ grid-template-columns: 1fr; }}
      .detail {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <main class="main">
      <header>
        <h1>{graph_id}</h1>
        <button id="fitBtn">适配</button>
        <button id="oneHopBtn">一跳邻域</button>
        <button id="twoHopBtn">二跳邻域</button>
        <button id="showAllBtn">显示全部</button>
        <button id="toggleGroupsBtn">隐藏分区</button>
        <button id="toggleAuxBtn">显示辅助关系</button>
        <button id="reflowBtn">重新排版</button>
        <button id="resetBtn">恢复默认视图</button>
        <span id="saveState" class="status">视图位置自动保存</span>
        <span id="loadError" class="load-error">未找到本地 G6：请补齐 vendor/g6/g6.min.js</span>
      </header>
      <div class="legend" aria-label="关系与审查状态图例">
        <span class="legend-item"><span class="legend-line prerequisite"></span>前置</span>
        <span class="legend-item"><span class="legend-line progressive"></span>递进</span>
        <span class="legend-item"><span class="legend-line auxiliary"></span>布局辅助</span>
        <span class="legend-item"><span class="legend-line symmetric"></span>对称关系同层</span>
        <span class="legend-item"><span class="legend-line cycle"></span>循环回边例外</span>
        <span class="legend-state pending">待审</span>
        <span class="legend-state revision">待修</span>
        <span id="contextLegend" class="legend-item">教材归属与关系语义分开表达</span>
      </div>
      <div id="graph"></div>
    </main>
    <aside id="detail" class="detail">
      <h2>尚未选择对象</h2>
      <div><span class="kv">提示：</span>点击节点、关系或章节容器查看详情。</div>
    </aside>
  </div>
  <script>
    window.GRAPH_REVIEW_PAYLOAD = {payload_json};
  </script>
  <script src="./vendor/g6/g6.min.js"></script>
  <script>
    let payload = window.GRAPH_REVIEW_PAYLOAD || {{}};
    const graphEl = document.getElementById('graph');
    const viewConfigKey = 'p2_g6_review_' + (payload.graph_id || 'default')
      + '_' + ((payload.metadata || {{}}).view_mode || 'textbook');
    let qtBridge = null;
    const toggleAuxBtn = document.getElementById('toggleAuxBtn');
    const toggleGroupsBtn = document.getElementById('toggleGroupsBtn');
    document.getElementById('contextLegend').textContent =
      ((payload.metadata || {{}}).view_mode === 'relations')
        ? '教材归属见节点详情；未决 contains 保留为待确认关系线'
        : '包含关系由章节大框嵌套表达；归属由引用卡片表达';
    const nodeStyle = {{
      container: {{ fill: '#eff6ff', stroke: '#2563eb', title: '#1e3a8a', meta: '#1d4ed8' }},
      concept: {{ fill: '#1f2937', stroke: '#0f172a', title: '#fff', meta: '#d6e0ef' }},
      property: {{ fill: '#35516b', stroke: '#243b53', title: '#fff', meta: '#dbe7f4' }},
      rule: {{ fill: '#5b21b6', stroke: '#4c1d95', title: '#fff', meta: '#ede9fe' }},
      method: {{ fill: '#176c3a', stroke: '#14532d', title: '#fff', meta: '#dcfce7' }},
      representation: {{ fill: '#d7f5f1', stroke: '#0f766e', title: '#0f4f49', meta: '#0f766e' }},
      problem_type: {{ fill: '#ffedd5', stroke: '#c2410c', title: '#9a3412', meta: '#c2410c' }},
      application: {{ fill: '#fff7ed', stroke: '#ea580c', title: '#9a3412', meta: '#c2410c' }},
    }};
    const nodeTypeLabel = {{
      container: '结构节点',
      chapter: '章节',
      section: '小节',
      subsection: '子小节',
      concept: '概念',
      property: '性质',
      rule: '规则',
      theorem: '定理',
      formula: '公式',
      method: '方法',
      representation: '表征',
      problem_type: '题型',
      application: '应用',
    }};
    const relationTypeLabel = {{
      contains: '包含',
      prerequisite: '前置',
      progressive: '递进',
      derives_to: '推导',
      explains: '解释',
      equivalent: '等价',
      parallel: '并列',
      contrast: '对比',
      applies_to: '应用于',
      represented_by: '表征为',
      layout_main_path: '主路径',
      layout_branch: '分支',
      layout_auxiliary: '侧挂',
    }};
    const reviewStatusLabel = {{
      pending: '待审查',
      accepted: '已通过',
      rejected: '已拒绝',
      needs_revision: '需修改',
      needs_expert_review: '需专家复核',
    }};
    const reviewStatusStyle = {{
      pending: {{ bg: '#fff7ed', text: '#9a3412', border: '#fdba74', edgeOpacity: 0.86 }},
      accepted: {{ bg: '#ecfdf3', text: '#166534', border: '#22c55e', edgeOpacity: 1 }},
      needs_revision: {{ bg: '#fff1f2', text: '#be123c', border: '#f43f5e', edgeOpacity: 0.92 }},
      needs_expert_review: {{ bg: '#f5f3ff', text: '#6d28d9', border: '#8b5cf6', edgeOpacity: 0.92 }},
      rejected: {{ bg: '#f8fafc', text: '#475569', border: '#94a3b8', edgeOpacity: 0.44 }},
      layout: {{ bg: '#f8fafc', text: '#475569', border: '#cbd5e1', edgeOpacity: 0.66 }},
    }};
    const roleLabel = {{
      main_path: '主路径',
      branch: '分支',
      auxiliary: '侧挂',
      structural: '子结构',
      fallback: '待确认',
      orphan: '独立',
      container: '容器',
    }};
    const fieldLabel = {{
      id: '对象编号',
      label: '显示名称',
      node_name: '规范名称',
      knowledgeType: '知识类型',
      roleLabel: '布局角色',
      role: '布局角色',
      comboId: '所属容器',
      comboType: '容器类型',
      structuralRole: '结构层级',
      structuralParentId: '直接父级',
      parentId: '上级容器',
      chapter: '章节',
      subject: '学科',
      grade: '年级',
      term: '册次',
      source: '来源节点',
      target: '目标节点',
      source_node_id: '来源节点',
      target_node_id: '目标节点',
      relationType: '关系类型',
      confidence: '置信度',
      review_status: '审查状态',
      reasoning_summary: '判断依据',
      source_locations: '来源位置',
      sourceLocation: '来源位置',
      businessNodeId: '知识点编号',
      relationId: '关系记录编号',
      membershipIds: '教材归属记录',
      membershipCount: '教材归属数量',
      contextScopeIds: '教材上下文',
      evidenceAnchorIds: '证据锚点',
      occurrenceIds: '出现记录',
      reasoningSummary: '判断依据',
      relationFamily: '关系族',
      rawRelationType: '原始关系类型',
      semanticsPending: '语义待确认',
      labelSuppressionReason: '标签抑制原因',
      is_layout_edge: '布局辅助关系',
      x: '横坐标',
      y: '纵坐标',
      layoutRank: '横向层级',
      layoutOrder: '同层顺序',
      layoutComponent: '布局分量',
      inLayoutCycle: '循环节点',
      directionality: '方向类别',
      layoutDirection: '布局方向',
      isCycleEdge: '循环回边',
    }};
    const edgeStyle = {{
      prerequisite: {{ stroke: '#1d4ed8', label: '前置', lineWidth: 4 }},
      progressive: {{ stroke: '#0f766e', label: '递进', lineWidth: 4 }},
      derives_to: {{ stroke: '#7c3aed', label: '推导', lineWidth: 3 }},
      explains: {{ stroke: '#64748b', label: '解释', lineWidth: 2, dash: [6, 6] }},
      equivalent: {{ stroke: '#475467', label: '等价', lineWidth: 2, arrowMode: 'both' }},
      parallel: {{ stroke: '#7c3aed', label: '并列', lineWidth: 2, dash: [8, 6], arrowMode: 'none' }},
      contrast: {{ stroke: '#b42318', label: '对比', lineWidth: 2, dash: [8, 6], arrowMode: 'both' }},
      applies_to: {{ stroke: '#b45309', label: '应用', lineWidth: 2, dash: [8, 5] }},
      represented_by: {{ stroke: '#0f766e', label: '表征', lineWidth: 2, dash: [10, 4, 2, 4] }},
      unresolved_contains: {{ stroke: '#b45309', label: '包含·含义待确认', lineWidth: 2.2, dash: [4, 5] }},
      unknown: {{ stroke: '#64748b', label: '关系类型待确认', lineWidth: 2, dash: [3, 5] }},
      layout_main_path: {{ stroke: '#2563eb', label: '主路径', lineWidth: 3 }},
      layout_branch: {{ stroke: '#64748b', label: '分支', lineWidth: 2, dash: [8, 6] }},
      layout_auxiliary: {{ stroke: '#b45309', label: '侧挂', lineWidth: 2, dash: [8, 5] }},
    }};
    let graph = null;
    let graphRendered = false;
    let dragSnapshot = null;
    let selectedNodeId = '';
    let neighborhoodSnapshot = null;
    let showAuxiliary = Boolean((payload.view_config || {{}}).show_auxiliary_edges ?? false);
    let showRelationGroups = Boolean((payload.view_config || {{}}).show_relation_groups ?? true);

    function syncAuxiliaryButton() {{
      toggleAuxBtn.classList.toggle('active', showAuxiliary);
      toggleAuxBtn.textContent = showAuxiliary ? '隐藏辅助关系' : '显示辅助关系';
    }}
    function syncGroupsButton() {{
      toggleGroupsBtn.classList.toggle('active', showRelationGroups);
      toggleGroupsBtn.textContent = showRelationGroups ? '隐藏分区' : '显示分区';
      toggleGroupsBtn.style.display = (payload.metadata || {{}}).view_mode === 'relations' ? '' : 'none';
    }}

    function clone(value) {{
      return JSON.parse(JSON.stringify(value || {{}}));
    }}
    function loadViewConfig() {{
      try {{
        return JSON.parse(localStorage.getItem(viewConfigKey) || '{{}}');
      }} catch (_err) {{
        return {{}};
      }}
    }}
    function initQtBridge() {{
      if (!window.qt) return;
      const connect = () => {{
        if (!window.QWebChannel) return;
        new QWebChannel(qt.webChannelTransport, (channel) => {{
          qtBridge = channel.objects.graphReviewBridge || null;
        }});
      }};
      if (window.QWebChannel) {{
        connect();
        return;
      }}
      const script = document.createElement('script');
      script.src = 'qrc:///qtwebchannel/qwebchannel.js';
      script.onload = connect;
      document.head.appendChild(script);
    }}
    function buildViewConfig(manualPositions, extra = {{}}) {{
      return {{
        graph_id: payload.graph_id || 'default',
        layout_revision: Number((payload.metadata || {{}}).layout_version || 4),
        layout_mode: (payload.view_config || {{}}).layout_mode || 'review_projection_recursive_v4',
        view_mode: (payload.metadata || {{}}).view_mode || (payload.view_config || {{}}).view_mode || 'textbook',
        manual_positions: manualPositions,
        show_auxiliary_edges: showAuxiliary,
        show_relation_groups: showRelationGroups,
        structure_fingerprint: String((payload.metadata || {{}}).structure_fingerprint || ''),
        updated_at: new Date().toISOString(),
        ...extra,
      }};
    }}
    function persistViewConfig(config) {{
      localStorage.setItem(viewConfigKey, JSON.stringify(config));
      if (qtBridge && qtBridge.saveViewConfig) {{
        qtBridge.saveViewConfig(JSON.stringify(config));
      }}
    }}
    function sourceData() {{
      const data = clone(payload);
      const stored = loadViewConfig();
      const embeddedConfig = data.view_config || {{}};
      const storedIsCurrent = Number(stored.layout_revision || 0)
        === Number((data.metadata || {{}}).layout_version || 4)
        && String(stored.view_mode || '') === String((data.metadata || {{}}).view_mode || stored.view_mode || '');
      const storedMatchesFingerprint = String(stored.structure_fingerprint || '')
        === String((data.metadata || {{}}).structure_fingerprint || '');
      const embeddedMatchesFingerprint = String(embeddedConfig.structure_fingerprint || '')
        === String((data.metadata || {{}}).structure_fingerprint || '');
      const lockedAutomaticLayout = Number((data.metadata || {{}}).layout_version || 0) >= 5;
      const manualPositions = lockedAutomaticLayout
        ? {{}}
        : embeddedConfig.storage_mode === 'desktop_file'
          ? (embeddedMatchesFingerprint ? embeddedConfig.manual_positions || {{}} : {{}})
          : (storedIsCurrent && storedMatchesFingerprint ? stored.manual_positions || {{}} : {{}});
      [...(data.nodes || []), ...(data.combos || [])].forEach((item) => {{
        const position = manualPositions[item.id];
        if (position && Number.isFinite(position.x) && Number.isFinite(position.y)) {{
          item.x = position.x;
          item.y = position.y;
        }}
      }});
      const realEdges = (data.edges || []).filter((edge) => !edge.is_layout_edge);
      const legacyLayoutEdges = (data.edges || []).filter((edge) => edge.is_layout_edge);
      const layoutEdges = [...legacyLayoutEdges, ...(data.layout_edges || [])];
      const relationGroups = (showRelationGroups ? (data.visual_groups || []) : []).map((item) => ({{
        ...item,
        label: item.title,
        isVisualRelationGroup: true,
        padding: [70, 28, 28, 28],
      }}));
      const visualGroupByNode = {{}};
      relationGroups.forEach((group) => (group.memberViewIds || []).forEach((nodeId) => {{
        visualGroupByNode[nodeId] = group.id;
      }}));
      return {{
        nodes: (data.nodes || []).map((node) => enrichNode({{
          ...node,
          comboId: node.comboId || visualGroupByNode[node.id] || '',
        }})),
        edges: [...realEdges, ...(showAuxiliary ? layoutEdges : [])].map(enrichEdge),
        combos: [...(data.combos || []), ...relationGroups].map(enrichCombo),
      }};
    }}
    function displayNodeType(type) {{
      return nodeTypeLabel[type] || type || '未分类';
    }}
    function normalizeReviewStatus(status) {{
      return status || 'pending';
    }}
    function displayReviewStatus(status) {{
      return reviewStatusLabel[normalizeReviewStatus(status)] || status || '未审查';
    }}
    function statusVisual(status) {{
      return reviewStatusStyle[normalizeReviewStatus(status)] || reviewStatusStyle.pending;
    }}
    function clipText(value, maxLength) {{
      const text = String(value || '');
      return text.length > maxLength ? `${{text.slice(0, maxLength - 1)}}…` : text;
    }}
    const textMeasureCanvas = document.createElement('canvas');
    const textMeasureContext = textMeasureCanvas.getContext('2d');
    function graphemes(value) {{
      const text = String(value || '');
      if (window.Intl && Intl.Segmenter) {{
        return [...new Intl.Segmenter('zh-CN', {{ granularity: 'grapheme' }}).segment(text)]
          .map((item) => item.segment);
      }}
      return Array.from(text);
    }}
    function clipTextToWidth(value, maxWidth, font) {{
      const parts = graphemes(value);
      if (!textMeasureContext) return clipText(value, Math.max(2, Math.floor(maxWidth / 16)));
      textMeasureContext.font = font;
      if (textMeasureContext.measureText(parts.join('')).width <= maxWidth) return parts.join('');
      const suffix = '…';
      while (parts.length && textMeasureContext.measureText(parts.join('') + suffix).width > maxWidth) {{
        parts.pop();
      }}
      return parts.join('') + suffix;
    }}
    function enrichNode(node) {{
      return {{
        ...node,
        type: 'edu-card',
        size: node.size || (node.knowledgeType === 'application' ? [220, 72] : [250, 86]),
        style: nodeStyle[node.knowledgeType] || nodeStyle.concept,
        reviewStyle: statusVisual(node.review_status),
      }};
    }}
    function enrichEdge(edge) {{
      const registered = (payload.relation_style_registry || {{}})[edge.semanticStyleKey || ''];
      const fallback = edgeStyle[edge.semanticStyleKey]
        || edgeStyle[edge.relationType]
        || edgeStyle.unknown;
      const spec = registered ? {{
        stroke: registered.stroke,
        label: registered.label,
        lineWidth: registered.line_width,
        dash: registered.dash,
        arrowMode: registered.arrow_mode,
      }} : fallback;
      const review = edge.is_layout_edge ? statusVisual('layout') : statusVisual(edge.review_status);
      const normalizedStatus = normalizeReviewStatus(edge.review_status);
      const reviewDash = edge.is_layout_edge
        ? spec.dash
        : normalizedStatus === 'needs_revision'
          ? [10, 5]
          : normalizedStatus === 'pending'
            ? [5, 4]
            : normalizedStatus === 'rejected'
              ? [2, 6]
              : spec.dash;
      const arrowPath = {{ path: G6.Arrow.triangle(8, 10, 0), fill: spec.stroke }};
      const arrowMode = spec.arrowMode || 'end';
      const edgeVisualStyle = {{
        stroke: spec.stroke,
        lineWidth: spec.lineWidth,
        lineDash: reviewDash,
        opacity: review.edgeOpacity,
      }};
      if (arrowMode === 'end' || arrowMode === 'both') edgeVisualStyle.endArrow = arrowPath;
      if (arrowMode === 'both') edgeVisualStyle.startArrow = arrowPath;
      const verticalRouting = ['vertical_neutral', 'vertical_cross_scope'].includes(edge.routing);
      const cycleRouting = edge.routing === 'cycle_back';
      const sharedRouting = edge.routing === 'shared_route' && Array.isArray(edge.routePoints);
      if (sharedRouting) {{
        delete edgeVisualStyle.endArrow;
        delete edgeVisualStyle.startArrow;
      }}
      return {{
        ...edge,
        type: sharedRouting ? 'shared-polyline-v6' : (cycleRouting ? 'quadratic' : (verticalRouting ? 'cubic-vertical' : 'cubic-horizontal')),
        controlPoints: sharedRouting ? (edge.controlPoints || []) : undefined,
        curveOffset: cycleRouting ? 72 : undefined,
        label: sharedRouting ? '' : (edge.labelVisible === false
          ? ''
          : (edge.labelText || (edge.is_layout_edge
            ? spec.label
            : `${{spec.label}} · ${{displayReviewStatus(edge.review_status)}}`))),
        style: edgeVisualStyle,
        labelCfg: {{
          position: 'middle',
          autoRotate: false,
          refX: 0,
          refY: edge.is_layout_edge ? -14 : -10,
          style: {{
            fill: review.text,
            fontSize: 12,
            fontWeight: 700,
            textAlign: 'center',
            textBaseline: 'middle',
            shadowColor: 'rgba(255, 255, 255, .96)',
            shadowBlur: 8,
            background: {{ fill: review.bg, stroke: review.border, padding: [4, 7, 4, 7], radius: 5 }},
          }},
        }},
      }};
    }}
    function enrichCombo(combo) {{
      if (combo.isVisualRelationGroup) {{
        const isolated = combo.kind === 'unconnected_region';
        return {{
          ...combo,
          type: 'rect',
          label: combo.title,
          style: {{
            fill: isolated ? '#f8fafc' : '#f6f8fb',
            stroke: isolated ? '#94a3b8' : '#9db3cc',
            lineWidth: 1.4,
            lineDash: isolated ? [6, 5] : undefined,
            radius: 12,
          }},
          labelCfg: {{
            refY: -Math.max(20, Number((combo.size || [0, 0])[1]) / 2 - 24),
            style: {{ fill: '#475569', fontSize: 15, fontWeight: 700 }},
          }},
        }};
      }}
      const review = statusVisual(combo.review_status);
      return {{
        ...combo,
        type: 'rect',
        label: `${{clipText(combo.label, 16)}} · ${{displayReviewStatus(combo.review_status)}}`,
        padding: combo.padding || [54, 28, 24, 28],
        style: {{
          fill: review.bg,
          stroke: review.border,
          lineWidth: 2.2,
          radius: 8,
          shadowColor: 'rgba(15, 23, 42, .10)',
          shadowBlur: 8,
          shadowOffsetY: 2,
        }},
        labelCfg: {{
          refY: -34,
          style: {{
            fill: review.text,
            fontSize: 17,
            fontWeight: 800,
            background: {{ fill: '#ffffff', stroke: review.border, padding: [4, 8, 4, 8], radius: 6 }},
          }},
        }},
      }};
    }}
    function registerCard() {{
      G6.registerNode('edu-card', {{
        draw(cfg, group) {{
          const [width, height] = cfg.size || [220, 76];
          const style = cfg.style || nodeStyle.concept;
          const review = cfg.reviewStyle || statusVisual(cfg.review_status);
          const keyShape = group.addShape('rect', {{
            attrs: {{
              x: -width / 2,
              y: -height / 2,
              width,
              height,
              radius: 7,
              fill: style.fill,
              stroke: style.stroke,
              lineWidth: 2,
              shadowColor: 'rgba(15, 23, 42, .12)',
              shadowBlur: 8,
              shadowOffsetY: 3,
              cursor: 'default',
            }},
          }});
          group.addShape('rect', {{
            attrs: {{
              x: -width / 2,
              y: -height / 2,
              width: 6,
              height,
              radius: 3,
              fill: review.border,
              cursor: 'default',
            }},
          }});
          group.addShape('rect', {{
            attrs: {{
              x: width / 2 - 78,
              y: -height / 2 + 9,
              width: 62,
              height: 22,
              radius: 11,
              fill: review.bg,
              stroke: review.border,
              lineWidth: 1,
              cursor: 'default',
            }},
          }});
          group.addShape('text', {{
            attrs: {{
              x: width / 2 - 47,
              y: -height / 2 + 20,
              text: displayReviewStatus(cfg.review_status),
              fill: review.text,
              fontSize: 12,
              fontWeight: 700,
              textAlign: 'center',
              textBaseline: 'middle',
              cursor: 'default',
            }},
            capture: false,
          }});
          group.addShape('text', {{
            attrs: {{
              x: -width / 2 + 16,
              y: -height / 2 + 30,
              text: `${{cfg.readingOrdinal ? cfg.readingOrdinal + '  ' : ''}}${{clipTextToWidth(cfg.label, width - 106, '800 16px Microsoft YaHei')}}`,
              fill: style.title,
              fontSize: 16,
              fontWeight: 800,
              textBaseline: 'middle',
              cursor: 'default',
            }},
            capture: false,
          }});
          group.addShape('text', {{
            attrs: {{
              x: -width / 2 + 16,
              y: height / 2 - 20,
              text: `${{displayNodeType(cfg.knowledgeType)}} | ${{cfg.roleLabel}}`,
              fill: style.meta,
              fontSize: 13,
              textBaseline: 'middle',
              cursor: 'default',
            }},
            capture: false,
          }});
          return keyShape;
        }},
        getAnchorPoints(cfg) {{
          return (cfg && cfg.anchorPoints)
            || [[0, 0.5], [1, 0.5], [0.5, 0], [0.5, 1]];
        }},
      }});
    }}
    function registerSharedEdge() {{
      G6.registerEdge('shared-polyline-v6', {{
        draw(cfg, group) {{
          const points = Array.isArray(cfg.routePoints) ? cfg.routePoints : [];
          const path = points.map((point, index) => [index ? 'L' : 'M', Number(point[0]), Number(point[1])]);
          const style = cfg.style || {{}};
          const keyShape = group.addShape('path', {{
            attrs: {{
              path,
              stroke: style.stroke || '#64748b',
              lineWidth: style.lineWidth || 2,
              lineDash: style.lineDash,
              opacity: style.opacity == null ? 1 : style.opacity,
            }},
            name: 'shared-route',
          }});
          const addArrow = (polygon, name) => {{
            if (!Array.isArray(polygon) || polygon.length < 3) return;
            const arrowPath = polygon.map((point, index) => [index ? 'L' : 'M', Number(point[0]), Number(point[1])]);
            arrowPath.push(['Z']);
            group.addShape('path', {{
              attrs: {{ path: arrowPath, fill: style.stroke || '#64748b', stroke: style.stroke || '#64748b', lineWidth: 1 }},
              name,
            }});
          }};
          addArrow(cfg.sourceArrowPoints, 'shared-source-arrow');
          addArrow(cfg.targetArrowPoints, 'shared-target-arrow');
          const showLabel = cfg.runtimeLabelVisible == null ? Boolean(cfg.labelVisible) : Boolean(cfg.runtimeLabelVisible);
          const labelRect = cfg.labelRect;
          if (showLabel && labelRect && cfg.labelText) {{
            group.addShape('rect', {{
              attrs: {{
                x: Number(labelRect.x), y: Number(labelRect.y),
                width: Number(labelRect.width), height: Number(labelRect.height),
                radius: 5, fill: '#fffdf8', stroke: style.stroke || '#64748b', lineWidth: .8,
              }},
              name: 'shared-label-background',
            }});
            group.addShape('text', {{
              attrs: {{
                x: Number(labelRect.x) + Number(labelRect.width) / 2,
                y: Number(labelRect.y) + Number(labelRect.height) / 2,
                text: cfg.labelText, fill: '#334155', fontSize: 12, fontWeight: 700,
                textAlign: 'center', textBaseline: 'middle',
              }},
              capture: false,
              name: 'shared-label-text',
            }});
          }}
          return keyShape;
        }},
      }});
    }}
    function boot() {{
      if (!window.G6) {{
        document.getElementById('loadError').style.display = 'inline';
        return;
      }}
      registerCard();
      registerSharedEdge();
      graph = new G6.Graph({{
        container: 'graph',
        width: graphEl.clientWidth,
        height: graphEl.clientHeight,
        fitView: false,
        fitViewPadding: [40, 60, 40, 60],
        groupByTypes: true,
        nodeStateStyles: {{
          selected: {{ stroke: '#2563eb', lineWidth: 3, shadowColor: '#2563eb', shadowBlur: 14 }},
          active: {{ stroke: '#2563eb', lineWidth: 3 }},
        }},
        edgeStateStyles: {{
          selected: {{ stroke: '#2563eb', lineWidth: 3 }},
          active: {{ stroke: '#2563eb', lineWidth: 3 }},
        }},
        comboStateStyles: {{
          selected: {{ stroke: '#2563eb', lineWidth: 3, shadowColor: '#2563eb', shadowBlur: 12 }},
          active: {{ stroke: '#2563eb', lineWidth: 3 }},
        }},
        modes: {{
          default: [
            'drag-canvas',
            'zoom-canvas',
            ...(((payload.metadata || {{}}).layout_version || 0) >= 5
              ? []
              : [{{ type: 'drag-node', updateEdge: true }}, {{ type: 'drag-combo' }}]),
            ...(((payload.metadata || {{}}).layout_version || 0) >= 5
              ? []
              : ['collapse-expand-combo']),
            'activate-relations',
          ],
        }},
      }});
      render({{ fitView: true }});
      graph.on('node:click', (event) => selectGraphItem('节点', 'node', event.item));
      graph.on('edge:click', (event) => selectGraphItem('关系', 'edge', event.item));
      graph.on('combo:click', (event) => selectGraphItem('结构容器', 'combo', event.item));
      graph.on('node:dragstart', rememberDragPositions);
      graph.on('combo:dragstart', rememberDragPositions);
      graph.on('node:dragend', validateDragAndSave);
      graph.on('combo:dragend', validateDragAndSave);
      if (((payload.metadata || {{}}).layout_version || 0) >= 5) {{
        document.getElementById('saveState').textContent = '共享避障路径已启用；节点拖动暂禁用，请使用重新布局';
      }}
      if (Number((payload.readability_report || {{}}).failed_edges || 0) > 0) {{
        document.getElementById('saveState').textContent =
          `布局已明确降级：${{payload.readability_report.failed_edges}} 条路径未通过合法性门禁，请筛选节点邻域`;
      }}
      let denseLabelsVisible = null;
      function syncLabelDensity(force = false) {{
        if (!graph || !graph.getZoom) return;
        const zoom = graph.getZoom();
        const next = denseLabelsVisible === true ? zoom >= 0.78 : zoom >= 0.92;
        if (!force && next === denseLabelsVisible) return;
        denseLabelsVisible = next;
        graph.getEdges().forEach((item) => {{
          const model = item.getModel();
          if (!model.labelSuppressionReason || model.labelSuppressionReason === 'overview_density') {{
            graph.updateItem(item, {{
              label: model.type === 'shared-polyline-v6' ? '' : (next ? (model.labelText || model.displayLabel || '') : ''),
              runtimeLabelVisible: Boolean(next && model.labelRect),
            }});
          }}
        }});
      }}
      syncLabelDensity(true);
      graph.on('viewportchange', () => syncLabelDensity(false));
      window.addEventListener('resize', () => graph.changeSize(graphEl.clientWidth, graphEl.clientHeight));
    }}
    function render(options = {{}}) {{
      const data = sourceData();
      if (!graphRendered) {{
        graph.data(data);
        graph.render();
        graphRendered = true;
      }} else {{
        graph.changeData(data);
      }}
      if (options.fitView) {{
        graph.fitView(30);
      }}
    }}
    window.updateGraphReviewPayload = function(nextPayload) {{
      payload = nextPayload || payload;
      if (payload.view_config && Object.prototype.hasOwnProperty.call(payload.view_config, 'show_auxiliary_edges')) {{
        showAuxiliary = Boolean(payload.view_config.show_auxiliary_edges);
        syncAuxiliaryButton();
      }}
      if (payload.view_config && Object.prototype.hasOwnProperty.call(payload.view_config, 'show_relation_groups')) {{
        showRelationGroups = Boolean(payload.view_config.show_relation_groups);
        syncGroupsButton();
      }}
      if (graph) {{
        render({{ fitView: false }});
      }}
      document.getElementById('saveState').textContent = '审查状态已更新';
      return true;
    }};
    window.getGraphReviewViewportCenter = function() {{
      if (!graph) return null;
      const canvasPoint = {{
        x: Math.max(1, graphEl.clientWidth / 2),
        y: Math.max(1, graphEl.clientHeight / 2),
      }};
      if (graph.getPointByCanvas) {{
        return graph.getPointByCanvas(canvasPoint.x, canvasPoint.y);
      }}
      const group = graph.getGroup();
      const matrix = group && group.getMatrix ? group.getMatrix() : null;
      if (!matrix) return {{ x: canvasPoint.x, y: canvasPoint.y }};
      const scaleX = matrix[0] || 1;
      const scaleY = matrix[4] || 1;
      return {{
        x: (canvasPoint.x - (matrix[6] || 0)) / scaleX,
        y: (canvasPoint.y - (matrix[7] || 0)) / scaleY,
      }};
    }};
    window.focusGraphReviewItem = function(itemId, itemType) {{
      if (!graph || !itemId) return false;
      const item = graph.findById(String(itemId));
      if (!item) return false;
      const allItems = []
        .concat(graph.getNodes ? graph.getNodes() : [])
        .concat(graph.getEdges ? graph.getEdges() : [])
        .concat(graph.getCombos ? graph.getCombos() : []);
      allItems.forEach((target) => {{
        if (target && graph.clearItemStates) graph.clearItemStates(target);
      }});
      if (graph.focusItem) {{
        graph.focusItem(item, true, {{ duration: 320, easing: 'easeCubic' }});
      }}
      if (graph.setItemState) {{
        graph.setItemState(item, 'selected', true);
        graph.setItemState(item, 'active', true);
      }}
      const model = item.getModel ? item.getModel() : {{}};
      if (itemType === 'node') selectedNodeId = String(model.id || itemId || '');
      showDetail(itemType === 'edge' ? '关系' : '节点', model);
      document.getElementById('saveState').textContent = '已定位到图谱对象';
      return true;
    }};
    window.getGraphReviewGeometryReport = function() {{
      const metadata = payload.metadata || {{}};
      if (!graph) return {{ ready: false, documentRevision: metadata.document_revision ?? null }};
      const bounds = (item) => {{
        const box = item && item.getBBox ? item.getBBox() : null;
        if (!box) return null;
        return {{
          x: Number(box.minX), y: Number(box.minY),
          width: Number(box.maxX - box.minX), height: Number(box.maxY - box.minY),
        }};
      }};
      const nodes = Object.fromEntries(
        graph.getNodes().map((item) => [String(item.getID()), bounds(item)])
      );
      const combos = Object.fromEntries(
        graph.getCombos().map((item) => [String(item.getID()), bounds(item)])
      );
      const edges = Object.fromEntries(graph.getEdges().map((item) => {{
        const model = item.getModel ? item.getModel() : {{}};
        const container = item.getContainer ? item.getContainer() : null;
        const shapes = container && container.get ? (container.get('children') || []) : [];
        const shapeByName = Object.fromEntries(shapes.map((shape) => [
          String((shape.get && shape.get('name')) || ''),
          {{ attrs: shape.attr ? shape.attr() : {{}}, bounds: bounds(shape) }},
        ]));
        return [String(item.getID()), {{
          route: shapeByName['shared-route'] || null,
          sourceArrow: shapeByName['shared-source-arrow'] || null,
          targetArrow: shapeByName['shared-target-arrow'] || null,
          labelBackground: shapeByName['shared-label-background'] || null,
          labelText: shapeByName['shared-label-text'] || null,
          runtimeLabelVisible: Boolean(model.runtimeLabelVisible),
        }}];
      }}));
      const violations = [];
      const contains = (outer, inner, tolerance = 2) => outer && inner
        && inner.x >= outer.x - tolerance
        && inner.y >= outer.y - tolerance
        && inner.x + inner.width <= outer.x + outer.width + tolerance
        && inner.y + inner.height <= outer.y + outer.height + tolerance;
      graph.getNodes().forEach((item) => {{
        const node = item.getModel ? item.getModel() : {{}};
        const parent = node.comboId && combos[node.comboId];
        if (node.comboId && !contains(parent, nodes[node.id])) {{
          violations.push({{ code: 'g6_node_out_of_combo', objectIds: [node.id, node.comboId] }});
        }}
      }});
      graph.getCombos().forEach((item) => {{
        const combo = item.getModel ? item.getModel() : {{}};
        const parent = combo.parentId && combos[combo.parentId];
        if (combo.parentId && !contains(parent, combos[combo.id])) {{
          violations.push({{ code: 'g6_combo_out_of_combo', objectIds: [combo.id, combo.parentId] }});
        }}
      }});
      const overlaps = (first, second, tolerance = 2) => first && second
        && Math.min(first.x + first.width, second.x + second.width)
          - Math.max(first.x, second.x) > tolerance
        && Math.min(first.y + first.height, second.y + second.height)
          - Math.max(first.y, second.y) > tolerance;
      const childrenByParent = {{}};
      graph.getNodes().forEach((item) => {{
        const node = item.getModel ? item.getModel() : {{}};
        if (!node.comboId) return;
        (childrenByParent[node.comboId] ||= []).push({{ id: node.id, box: nodes[node.id] }});
      }});
      graph.getCombos().forEach((item) => {{
        const combo = item.getModel ? item.getModel() : {{}};
        if (!combo.parentId) return;
        (childrenByParent[combo.parentId] ||= []).push({{ id: combo.id, box: combos[combo.id] }});
      }});
      Object.entries(childrenByParent).forEach(([parentId, children]) => {{
        for (let first = 0; first < children.length; first += 1) {{
          for (let second = first + 1; second < children.length; second += 1) {{
            if (overlaps(children[first].box, children[second].box)) {{
              violations.push({{
                code: 'g6_sibling_overlap',
                objectIds: [children[first].id, children[second].id, parentId],
              }});
            }}
          }}
        }}
      }});
      graph.getEdges().forEach((item) => {{
        const model = item.getModel ? item.getModel() : {{}};
        const measured = edges[String(item.getID())] || {{}};
        if (model.type !== 'shared-polyline-v6') return;
        if (!measured.route) {{
          violations.push({{ code: 'g6_shared_route_missing', objectIds: [String(item.getID())] }});
        }}
        const expectedTargetArrow = Array.isArray(model.targetArrowPoints) && model.targetArrowPoints.length >= 3;
        const expectedSourceArrow = Array.isArray(model.sourceArrowPoints) && model.sourceArrowPoints.length >= 3;
        if (expectedTargetArrow !== Boolean(measured.targetArrow)) {{
          violations.push({{ code: 'g6_target_arrow_mismatch', objectIds: [String(item.getID())] }});
        }}
        if (expectedSourceArrow !== Boolean(measured.sourceArrow)) {{
          violations.push({{ code: 'g6_source_arrow_mismatch', objectIds: [String(item.getID())] }});
        }}
        const hasLabelShapes = Boolean(measured.labelBackground || measured.labelText);
        if (!measured.runtimeLabelVisible && hasLabelShapes) {{
          violations.push({{ code: 'g6_empty_label_background', objectIds: [String(item.getID())] }});
        }}
        if (measured.runtimeLabelVisible && model.labelRect
          && (!measured.labelBackground || !measured.labelText)) {{
          violations.push({{ code: 'g6_visible_label_missing', objectIds: [String(item.getID())] }});
        }}
      }});
      return {{
        ready: true,
        documentId: metadata.document_id || payload.graph_id || '',
        documentRevision: metadata.document_revision ?? null,
        structureFingerprint: metadata.structure_fingerprint || '',
        projectionSchemaVersion: metadata.projection_schema_version || null,
        geometrySpecificationVersion: metadata.geometry_specification_version || null,
        measurementCoordinateSystem: 'g6-logical-canvas',
        tolerance: 2,
        nodes,
        combos,
        edges,
        violations,
      }};
    }};
    function rememberDragPositions() {{
      if (!graph) return;
      dragSnapshot = {{}};
      [...graph.getCombos(), ...graph.getNodes()].forEach((item) => {{
        const model = item.getModel();
        if (Number.isFinite(model.x) && Number.isFinite(model.y)) {{
          dragSnapshot[model.id] = {{ x: model.x, y: model.y }};
        }}
      }});
    }}
    function restoreDragPositions() {{
      if (!graph || !dragSnapshot) return;
      [...graph.getCombos(), ...graph.getNodes()].forEach((item) => {{
        const model = item.getModel();
        const position = dragSnapshot[model.id];
        if (position) graph.updateItem(item, position);
      }});
      if (graph.refresh) graph.refresh();
    }}
    function validateDragAndSave() {{
      if (!graph) return;
      window.requestAnimationFrame(() => {{
        const report = window.getGraphReviewGeometryReport();
        const reportEl = document.getElementById('geometryReportJson');
        if (reportEl) reportEl.textContent = JSON.stringify(report);
        if ((report.violations || []).length) {{
          restoreDragPositions();
          document.getElementById('saveState').textContent = '位置越界或重叠，已恢复拖拽前位置';
          dragSnapshot = null;
          return;
        }}
        dragSnapshot = null;
        saveViewConfig();
      }});
    }}
    function showDetail(kind, model) {{
      const hiddenKeys = new Set([
        'style', 'labelCfg', 'type', 'size', 'reviewStyle', 'anchorPoints',
        'routePoints', 'controlPoints', 'x', 'y', 'curveOffset',
      ]);
      const rows = Object.entries(model)
        .filter(([key]) => !hiddenKeys.has(key))
        .slice(0, 28)
        .map(([key, value]) => `<div><span class="kv">${{fieldLabel[key] || key}}：</span>${{formatDetailValue(key, value)}}</div>`)
        .join('');
      document.getElementById('detail').innerHTML = `<h2>${{escapeHtml(kind)}}：${{escapeHtml(model.label || model.id)}}</h2>${{rows}}`;
    }}
    function selectGraphItem(kind, itemType, item) {{
      if (!graph || !item) return;
      const allItems = []
        .concat(graph.getNodes ? graph.getNodes() : [])
        .concat(graph.getEdges ? graph.getEdges() : [])
        .concat(graph.getCombos ? graph.getCombos() : []);
      allItems.forEach((target) => graph.setItemState(target, 'selected', false));
      graph.setItemState(item, 'selected', true);
      const model = item.getModel ? item.getModel() : {{}};
      if (itemType === 'node') selectedNodeId = String(model.id || '');
      showDetail(kind, model);
      if (qtBridge && qtBridge.selectGraphItem) {{
        const businessId = itemType === 'node'
          ? (model.businessNodeId || model.id || '')
          : itemType === 'edge'
            ? (model.relationId || model.id || '')
            : (model.businessScopeId || model.id || '');
        qtBridge.selectGraphItem(itemType, String(businessId), String(model.id || ''));
      }}
    }}
    function escapeHtml(value) {{
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }}
    function formatDetailValue(key, value) {{
      let displayValue = value;
      if (key === 'knowledgeType') displayValue = displayNodeType(value);
      else if (key === 'relationType') displayValue = relationTypeLabel[value] || value || '未标注';
      else if (key === 'review_status') displayValue = displayReviewStatus(value);
      else if (key === 'role') displayValue = roleLabel[value] || value || '未标注';
      else if (key === 'comboType') displayValue = displayNodeType(value);
      else if (key === 'structuralRole') displayValue = displayNodeType(value);
      else if (key === 'confidence' && typeof value === 'number') displayValue = `${{Math.round(value * 100)}}%`;
      else if (key === 'is_layout_edge') displayValue = value ? '是' : '否';
      else if (Array.isArray(value)) displayValue = value.length ? value.join('、') : '无';
      else if (value && typeof value === 'object') displayValue = JSON.stringify(value);
      return escapeHtml(displayValue);
    }}
    function saveViewConfig() {{
      if (!graph) return;
      const manualPositions = {{}};
      if (Number((payload.metadata || {{}}).layout_version || 0) < 5) {{
        graph.getNodes().forEach((item) => {{
          const model = item.getModel();
          manualPositions[model.id] = {{ x: model.x, y: model.y }};
        }});
        graph.getCombos().forEach((item) => {{
          const model = item.getModel();
          if (Number.isFinite(model.x) && Number.isFinite(model.y)) {{
            manualPositions[model.id] = {{ x: model.x, y: model.y }};
          }}
        }});
      }}
      payload.view_config = {{ ...(payload.view_config || {{}}), ...buildViewConfig(manualPositions), storage_mode: (payload.view_config || {{}}).storage_mode }};
      persistViewConfig(buildViewConfig(manualPositions));
      document.getElementById('saveState').textContent = '视图位置已保存';
    }}
    document.getElementById('fitBtn').onclick = () => graph && graph.fitView(30);
    function captureViewport() {{
      if (!graph) return null;
      const group = graph.getGroup ? graph.getGroup() : null;
      const matrix = group && group.getMatrix ? group.getMatrix() : null;
      return matrix ? Array.from(matrix) : null;
    }}
    function restoreViewport(matrix) {{
      if (!graph || !matrix) return false;
      const group = graph.getGroup ? graph.getGroup() : null;
      if (!group || !group.setMatrix) return false;
      group.setMatrix(matrix);
      if (graph.paint) graph.paint();
      return true;
    }}
    function clearGraphSelection() {{
      if (!graph) return;
      const allItems = []
        .concat(graph.getNodes ? graph.getNodes() : [])
        .concat(graph.getEdges ? graph.getEdges() : [])
        .concat(graph.getCombos ? graph.getCombos() : []);
      allItems.forEach((item) => {{
        if (graph.clearItemStates) graph.clearItemStates(item);
      }});
      selectedNodeId = '';
      document.getElementById('detail').innerHTML = '<h2>尚未选择对象</h2><p>提示：点击节点、关系或分区查看详情。</p>';
    }}
    function showNeighborhood(maxHops) {{
      if (!graph || !selectedNodeId) {{
        document.getElementById('saveState').textContent = '请先选择一个知识点，再展开邻域';
        return;
      }}
      if (!neighborhoodSnapshot) {{
        neighborhoodSnapshot = {{
          viewport: captureViewport(),
          selectedNodeId,
        }};
      }}
      const adjacency = new Map();
      (payload.nodes || []).forEach((node) => adjacency.set(String(node.id), new Set()));
      (payload.edges || []).filter((edge) => !edge.is_layout_edge).forEach((edge) => {{
        const source = String(edge.source || '');
        const target = String(edge.target || '');
        if (!adjacency.has(source)) adjacency.set(source, new Set());
        if (!adjacency.has(target)) adjacency.set(target, new Set());
        adjacency.get(source).add(target);
        adjacency.get(target).add(source);
      }});
      const visible = new Set([selectedNodeId]);
      let frontier = new Set([selectedNodeId]);
      for (let hop = 0; hop < maxHops; hop += 1) {{
        const next = new Set();
        frontier.forEach((nodeId) => {{
          (adjacency.get(nodeId) || []).forEach((neighbor) => {{
            if (!visible.has(neighbor)) next.add(neighbor);
            visible.add(neighbor);
          }});
        }});
        frontier = next;
      }}
      graph.getNodes().forEach((item) => {{
        const show = visible.has(String(item.getID()));
        if (show) graph.showItem(item); else graph.hideItem(item);
      }});
      graph.getEdges().forEach((item) => {{
        const model = item.getModel ? item.getModel() : {{}};
        const show = visible.has(String(model.source || '')) && visible.has(String(model.target || ''));
        if (show) graph.showItem(item); else graph.hideItem(item);
      }});
      graph.getCombos().forEach((item) => graph.showItem(item));
      graph.fitView(40);
      document.getElementById('saveState').textContent =
        `已显示 ${{maxHops}} 跳邻域：${{visible.size}} 个知识点；方向和全部关系可在详情中核对`;
    }}
    function showAllGraphItems() {{
      if (!graph) return;
      [...graph.getNodes(), ...graph.getEdges(), ...graph.getCombos()].forEach((item) => graph.showItem(item));
      const snapshot = neighborhoodSnapshot;
      neighborhoodSnapshot = null;
      if (!snapshot || !restoreViewport(snapshot.viewport)) graph.fitView(30);
      if (snapshot && snapshot.selectedNodeId) {{
        const item = graph.findById(String(snapshot.selectedNodeId));
        if (item) graph.setItemState(item, 'selected', true);
        selectedNodeId = String(snapshot.selectedNodeId);
      }}
      document.getElementById('saveState').textContent = '已恢复显示全部知识点与关系';
    }}
    document.getElementById('oneHopBtn').onclick = () => showNeighborhood(1);
    document.getElementById('twoHopBtn').onclick = () => showNeighborhood(2);
    document.getElementById('showAllBtn').onclick = showAllGraphItems;
    document.getElementById('reflowBtn').onclick = () => {{
      if (qtBridge && qtBridge.requestRelayout) {{
        neighborhoodSnapshot = null;
        document.getElementById('saveState').textContent = '正在请求重新排版……';
        qtBridge.requestRelayout();
      }} else {{
        document.getElementById('saveState').textContent = '独立 HTML 不含后端布局引擎；可使用适配和恢复默认视图';
      }}
    }};
    document.getElementById('resetBtn').onclick = () => {{
      neighborhoodSnapshot = null;
      clearGraphSelection();
      showAuxiliary = false;
      showRelationGroups = (payload.metadata || {{}}).view_mode === 'relations';
      syncAuxiliaryButton();
      syncGroupsButton();
      payload.view_config = {{ ...(payload.view_config || {{}}), ...buildViewConfig({{}}), manual_positions: {{}} }};
      persistViewConfig(buildViewConfig({{}}, {{ reset_layout: true }}));
      document.getElementById('saveState').textContent = '已恢复默认视图';
      if (graph) render({{ fitView: true }});
    }};
    document.getElementById('toggleAuxBtn').onclick = (event) => {{
      showAuxiliary = !showAuxiliary;
      syncAuxiliaryButton();
      if (graph) {{
        render({{ fitView: false }});
        saveViewConfig();
      }}
    }};
    toggleGroupsBtn.onclick = () => {{
      showRelationGroups = !showRelationGroups;
      syncGroupsButton();
      if (graph) {{
        render({{ fitView: false }});
        saveViewConfig();
      }}
    }};
    document.addEventListener('keydown', (event) => {{
      if (event.key !== 'Escape') return;
      if (neighborhoodSnapshot) showAllGraphItems();
      clearGraphSelection();
      document.getElementById('saveState').textContent = '已取消选择';
    }});
    syncAuxiliaryButton();
    syncGroupsButton();
    initQtBridge();
    boot();
    setTimeout(() => {{
      const existing = document.getElementById('geometryReportJson');
      const reportNode = existing || document.createElement('pre');
      reportNode.id = 'geometryReportJson';
      reportNode.hidden = true;
      reportNode.textContent = JSON.stringify(
        window.getGraphReviewGeometryReport ? window.getGraphReviewGeometryReport() : {{ ready: false }}
      );
      if (!existing) document.body.appendChild(reportNode);
    }}, 300);
  </script>
</body>
</html>
"""
