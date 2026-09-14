from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
P4_ROOT = PROJECT_ROOT.parent / "P4_知识图谱与自适应引擎项目"
if not P4_ROOT.exists() and len(PROJECT_ROOT.parents) > 1:
    candidate = PROJECT_ROOT.parents[1] / "P4_知识图谱与自适应引擎项目"
    if candidate.exists():
        P4_ROOT = candidate

sys.path.insert(0, str(PROJECT_ROOT / "src"))
if P4_ROOT.exists():
    sys.path.insert(0, str(P4_ROOT))

from textbook_builder.project_cli import main


if __name__ == "__main__":
    main()
