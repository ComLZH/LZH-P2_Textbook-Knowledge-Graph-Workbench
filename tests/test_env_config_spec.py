from __future__ import annotations

import sys
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.utils.env_config import env_bool, env_value, load_dotenv_file


def test_dotenv_loader_reads_model_settings() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"env_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        env_path = temp_dir / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "TEXTBOOK_BUILDER_LLM_API_KEY=sk-test",
                    "TEXTBOOK_BUILDER_LLM_MODEL=qwen3.6-plus",
                    "TEXTBOOK_BUILDER_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "TEXTBOOK_BUILDER_LLM_ENABLE_THINKING=false",
                ]
            ),
            encoding="utf-8",
        )
        values = load_dotenv_file(env_path)

        assert values["TEXTBOOK_BUILDER_LLM_API_KEY"] == "sk-test"
        assert env_value("TEXTBOOK_BUILDER_LLM_MODEL", dotenv_values=values) == "qwen3.6-plus"
        assert env_bool(values["TEXTBOOK_BUILDER_LLM_ENABLE_THINKING"]) is False
    finally:
        rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_dotenv_loader_reads_model_settings()
    print("PASS env config tests")
