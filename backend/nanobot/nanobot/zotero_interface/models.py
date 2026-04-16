"""给 onboard 向导使用的“模型信息”辅助函数。

模型数据库 / 自动补全功能目前暂时禁用，因为正在替换 litellm。
这里保留了所有对外公开函数的签名，确保调用方无需修改也能正常运行。
"""

from __future__ import annotations

from typing import Any


def get_all_models() -> list[str]:
    return []


def find_model_info(model_name: str) -> dict[str, Any] | None:
    return None


def get_model_context_limit(model: str, provider: str = "auto") -> int | None:
    return None


def get_model_suggestions(partial: str, provider: str = "auto", limit: int = 20) -> list[str]:
    return []


def format_token_count(tokens: int) -> str:
    """把 token 数量格式化为更易读的显示文本（例如 200000 -> '200,000'）。"""
    return f"{tokens:,}"
