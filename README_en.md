<div align="center">

# LZH-P2 Textbook Knowledge Graph Workbench

### (Textbook-Knowledge-Graph-Workbench_LZH)

<p>
  <a href="README.md">简体中文</a> | <b>English</b>
</p>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg)
![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)
![GUI: PySide6](https://img.shields.io/badge/GUI-PySide6-41CD52.svg)
![Layout: ELK--Layered](https://img.shields.io/badge/Layout-ELK--Layered-orange.svg)
![Author: LZH](https://img.shields.io/badge/Author-LZH-red.svg)

<p>
  <b>Grounding on textbook structure, centered on canonical knowledge concepts, powered by typed semantic relations.</b>
  <br />
  A Windows workbench for K-12 textbook knowledge modeling, first-pass independent extraction, single-tier relation grouping, 2D orthogonal obstacle-avoiding layout, and teacher review.
</p>

</div>

---

## 📖 Why I built this

In educational digitization and LLM-driven applications, existing mind-mapping tools and automated LLM outlining services are capable of turning textbooks or instructional materials into hierarchical content outlines. They excel at preserving chapter tables of contents, headings, and paragraph order, which is ideal for reading navigation and high-level overviews.

**However, "completely organizing a textbook" does not equal "building a computable cognitive knowledge graph."**

The physical arrangement of textbook material is primarily tailored for linear teaching progression. It cannot directly answer fundamental questions such as:
- Which content elements are stable, reusable, and testable **canonical knowledge units**?
- Is the relationship between two concepts **containment, prerequisite, progression**, or **derivation, representation, application**?
- When a textbook introduces Concept A before Property B, is it merely an editorial choice or a strict cognitive prerequisite?
- Which relationships directly originate from textbook textual evidence, which are model inferences, and which remain to be verified by human teachers?

**The LZH-P2 project was created specifically to bridge this gap:**
It serves as a professional **knowledge graph construction workbench**. Through a closed-loop pipeline comprising "first-pass independent node extraction + local relational clues + canonical alias normalization + single-tier relation grouping + ELK 2D orthogonal layout + teacher review workbench," it transforms unstructured textbook PDFs/images into high-quality knowledge graphs with rigorous evidence anchors and human-in-the-loop auditability.

---

## ✨ What can you do with it?

| Need | Capability |
| :--- | :--- |
| **Multi-format normalization** | Ingest PDF (native text layer first, scanned page OCR fallback), PNG, JPG, and structured JSON with page range slicing |
| **Extract canonical knowledge units** | Avoid indiscriminately promoting classroom activities and exercises to nodes; extract canonical concepts, properties, and theorems |
| **Discover rich semantic relations** | Break through flat tree hierarchies by extracting typed dependencies: containment, prerequisite, progression, derivation, representation, application, parallel, and contrast |
| **Dual-view coordinated layout** | **Relation Exploration (default)** and **Textbook Localization (auxiliary)** modes; Qt and G6 renderers share identical geometry |
| **Textbook-grade 2D automated layout** | Integrated Eclipse Layout Kernel (ELK) left-to-right layered layout with single-tier visual grouping, ordered fan-out, and orthogonal routing |
| **Human-in-the-loop review** | Every node and edge is linked to textbook page numbers, textual evidence, and confidence scores; supports revision history and 4D quality gates |
| **Downstream publishing** | Export normalized Excel entity/relation workbooks and `layer_mapping.json`, published atomically to adaptive learning engines |

---

## 🖼️ UI & Core Workflow Showcase

### 1. Full Workbench & Dual-View Synchronized Review
The left panel features a self-developed high-fidelity PDF/textbook renderer (supporting smooth zoom and cursor-centered positioning). The right panel hosts an interactive knowledge graph reviewer and review table, aligning textbook source evidence with graph relations in real time.

![Full Workbench Dual-View Synchronized Review](docs/相关使用截图/01_工作台全貌_双视图联动审查.png)
*Full Workbench View: Left panel displays electronic textbook source pages and diagrams; right panel displays relation-driven layered knowledge graph, status badges, and the review table below.*

### 2. Deep Graph Review & Property Drawer
In Relation Exploration mode, knowledge points are clustered into single-tier visual relation groups (Visual Relation Group). Clicking any node opens a property drawer on the right to inspect stable object IDs, knowledge types, textbook context, and textual evidence anchors.

![Graph Single-Tier Grouping and Property Drawer](docs/相关使用截图/02_知识图谱深入审查与属性抽屉.png)
*Graph Review: Illustrates the visual grouping of "13.2.1 Triangle Sides" and "13.2.2 Altitudes, Medians, and Angle Bisectors", while the drawer presents detailed metadata and evidence chains.*

### 3. Asynchronous Pipeline & Real-Time Progress Feedback
After clicking [Start Analysis], the system enters an asynchronous, non-blocking extraction state. The main window remains responsive, while a progress dialog details the active pipeline phase (such as first-pass independent node and local clue extraction) and percentage progress, with cancellation support at any time.

![Extraction Pipeline and Progress Feedback](docs/相关使用截图/04_流水线抽取与分析进度指示.png)
*Pipeline Execution: Displays real-time multi-stage processing status while keeping the primary interface interactive.*

---

## 🖥️ Quick start

### 1. Download and launch

Download and extract the repository ZIP, or clone the repository. In the project root, double-click:

👉 **`启动教材知识图谱工作台.cmd`**

- **System Requirements**: Windows 10 / Windows 11 (x64) and **Python 3.10+**.
- **Launcher Mechanism**: The script is built with pure ASCII standards to prevent code-page pointer offset issues; the built-in detector discovers Conda virtual environments, active environments, or system Python, cleanly launching the PySide6 workbench in UTF-8.

Upon launch, the application displays the clean initialization and configuration screen:

![Startup and Textbook Import Configuration Screen](docs/相关使用截图/03_初始化与教材导入配置页面.png)
*Startup Screen: Configure textbook source, subject, grade, term, page range, and remote LLM or OCR runtime parameters.*

---

### 2. Core build and review workflow

1. **Import Textbook**: Click [Import Textbook] at the top (`Ctrl+O`), and select an electronic textbook PDF or image from `textbook/`.
2. **Select Page Range**: In the left configuration panel, specify start and end pages (2–3 pages are recommended for an initial trial).
3. **Run Knowledge Extraction**: Click [Start Analysis] (`Ctrl+Enter`). The pipeline automatically extracts text, discovers candidates, normalizes aliases, and builds relations.
4. **Human Review**: Select nodes in the graph to verify textbook textual evidence. Accept, reject, or manually supplement candidate relations.
5. **Quality Gate & Export**: Click [Export Formal Graph] (`Ctrl+Shift+S`). After passing 4-dimensional integrity checks, the formal Excel workbook and `layer_mapping.json` will be exported.

---

### 3. Command line & manual running

If you prefer using the command line or integrating into existing Python workflows:

```powershell
# 1. Install core dependencies
pip install -r requirements.txt

# (Optional) Install Windows OCR extension
pip install -r requirements-windows-ocr.txt

# 2. Launch the desktop workbench
python run_workbench.py

# 3. Run the automated test suite (102 specification tests)
python run_project.py tests
```

---

## ⚡ Layout Engine Runtime (ELK Layout Engine)

To resolve tangled edges, card collisions, and unclear hierarchies, this project integrates the **Eclipse Layout Kernel (ELK)** orthogonal layered layout engine:

- **Automatic System Node.js Detection**: If Node.js (v18+) is already installed on your system, the workbench **automatically detects and reuses it**, requiring zero configuration.
- **One-Click Portable Node.js Setup**: If Node.js is not installed, simply run the included setup script in PowerShell:
  ```powershell
  .\scripts\setup_runtime.ps1
  ```
  The script downloads the official portable Node.js archive and extracts `node.exe` into `vendor/layout_runtime/node/node.exe`.
- **Heuristic Fallback Mechanism**: In environments where Node.js is entirely unavailable, the system smoothly degrades to an internal Python heuristic layout algorithm to ensure uninterrupted operation.

---

## 🛡️ Review, Data Safety & Boundaries

Before deploying this tool, please review its safety design and operational boundaries:

- **Textbook Copyright Notice**: School textbooks are strictly protected under copyright law. As a knowledge engineering tool, **this open-source repository does not distribute or bundle any proprietary textbook files**. Users must acquire materials legitimately for localized educational and academic use.
- **API Key Security & Sanitization**: Hardcoding API keys in source code or repositories is strictly forbidden. To call remote LLM providers, copy `.env.example` to `.env` and manage your credentials privately.
- **Necessity of Teacher Review**: LLM-extracted knowledge points and semantic relations serve as candidate drafts. Even after passing four-dimensional automated quality gates, they must be audited by qualified educators prior to formal deployment.
- **Complex Dependency Evolution**: For dense cyclic cross-links and extended progressive chains, the automated layout engine is under continuous optimization (as illustrated below); the current release substantially reduces visual overlaps through single-tier relation grouping and ordered fan-out.

![Complex Dependency Layout Evolution](docs/相关使用截图/05_复杂关系演进场景说明.png)
*Layout Boundaries & Evolution: Demonstration of orthogonal routing under dense cross-dependencies; heuristic and layered routing algorithms are actively iterated.*

---

## 📁 Project Structure & Technical Docs

```text
LZH-P2_Textbook-Knowledge-Graph-Workbench/
├── 启动教材知识图谱工作台.cmd         # Windows one-click launcher (ASCII-safe)
├── run_workbench.ps1                # PowerShell core bootstrap script (UTF-8 with BOM)
├── run_workbench.py                 # Desktop workbench GUI entry point
├── run_project.py                   # Command-line project dispatcher (tests / batch)
├── requirements.txt                 # Core Python dependency specifications
├── requirements-windows-ocr.txt     # Optional PaddleOCR dependency specifications
├── .env.example                     # Environment configuration template
├── LICENSE                          # MIT Open Source License
├── README.md                        # Chinese Documentation (Default)
├── README_en.md                     # English Documentation
├── config/                          # Application metadata (app_info.json)
├── src/                             # Core algorithms and GUI source code
│   └── textbook_builder/
│       ├── desktop_app.py           # Workbench application window and controller
│       ├── geometry/                # ELK and orthogonal routing geometry core
│       ├── models/                  # Construction-phase DTOs and data contracts
│       ├── pipeline/                # Extraction, merging, and normalization pipeline
│       ├── readers/                 # PDF / image / JSON document readers
│       ├── review_views/            # G6 / Qt dual-view projection builders
│       └── services/                # Offline / online model and session services
├── tests/                           # Automated specification tests (test_*_spec.py)
├── scripts/                         # Utilities (setup_runtime, benchmarks, UI capture)
├── vendor/                          # External layout libraries (elkjs + notices)
├── docs/                            # Comprehensive architectural and evolution docs
│   └── 相关使用截图/                # High-resolution screenshots from actual runtime
├── storage/                         # Local runtime workspace (protected by .gitignore)
└── textbook/                        # User textbook input folder (with instructions, no copyrighted PDFs)
```

**Recommended Technical Reading**:
- Foundation: [Documentation Navigation](docs/00_文档导航.md), [Current Implementation Status](docs/01_当前实现状态.md), [System Architecture & Data Flows](docs/02_系统架构与工作台数据流.md), [Data Contracts & Interfaces](docs/03_数据契约与P4接口.md), [Environment & Security](docs/04_运行环境配置与安全.md), [Known Limitations & Roadmap](docs/05_测试验收已知限制与下一步.md).
- Architecture & Reports: [Project Overview Paper](docs/14_P2项目定位综述与公开介绍稿.md), [Semantic Separation Optimization Plan](docs/16_教材归属与知识语义分离及双视图递归布局优化方案.md), [Relation Exploration Implementation Report](docs/19_知识点中心关系探索与二维自动布局实施完成报告_20260911.md), [Relation Partitioning Readability Report](docs/21_关系分区与左向右自动布局可读性优化实施完成报告_20260913.md).

---

## 🧪 Running Tests

Execute from the project root:

```powershell
python run_project.py tests
```

The test suite contains 23 specification modules with 102 automated checkpoints, verifying:
- Node and chapter normalization and hierarchical projection algorithms;
- Relation partitioning, ordered fan-out, and obstacle-avoidance hard gates;
- Single-process batch ELK execution across multiple connected components;
- Desktop workbench state machine, session undo history, and atomic commits;
- Formal graph export schema and downstream import contracts.

---

## 📄 License

This project is developed by **LZH (刘钊昊)** and released under the [MIT License](LICENSE). Contributions, feedback, and issue reports via GitHub Issues are welcome.

---

## 📮 Contact Author

For usage feedback, technical discussions, or educational technology collaboration, feel free to get in touch:
📧 **[comlzh@outlook.com](mailto:comlzh@outlook.com)**
