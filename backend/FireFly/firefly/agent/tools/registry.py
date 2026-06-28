"""用于动态管理工具的注册表。"""

from typing import Any

from firefly.agent.tools.base import Tool

_EXECUTOR_CATALOG_DESC_MAX = 240


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册工具。"""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """按名称注销工具。"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """按名称获取工具。"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """从 OpenAI 或扁平 schema 中提取规范化工具名。"""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """按稳定顺序获取工具定义，提升提示词缓存友好性。

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended.
        """
        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        
        # 过滤掉浏览器相关工具
        browser_tool_prefixes = ("browser_", "cursor-ide-browser-")
        
        for schema in definitions:
            name = self._schema_name(schema)
            
            # 跳过浏览器工具
            if any(name.startswith(prefix) for prefix in browser_tool_prefixes):
                continue
                
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        return builtins + mcp_tools

    def build_executor_catalog(self, *, exclude: frozenset[str] | None = None) -> str:
        """Build a compact name+description list for orchestrator spawn delegation."""
        skip = exclude or frozenset()
        lines: list[str] = ["<executor_tools>"]
        for schema in self.get_definitions():
            name = self._schema_name(schema)
            if not name or name in skip:
                continue
            fn = schema.get("function")
            desc = fn.get("description", "") if isinstance(fn, dict) else schema.get("description", "")
            desc = " ".join(str(desc).split())
            if len(desc) > _EXECUTOR_CATALOG_DESC_MAX:
                desc = desc[: _EXECUTOR_CATALOG_DESC_MAX - 3] + "..."
            lines.append(
                f'  <tool name="{_escape_xml(name)}">{_escape_xml(desc)}</tool>'
            )
        lines.append("</executor_tools>")
        return "\n".join(lines)

    def prepare_call(
        self,
        name: str,
        params: dict[str, Any],
    ) -> tuple[Tool | None, dict[str, Any], str | None]:
        """解析、类型转换并校验单次工具调用。"""
        # 防御非法参数类型（例如 list 误传成 dict）
        if not isinstance(params, dict) and name in ('write_file', 'read_file'):
            return None, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, got {type(params).__name__}. "
                "Use named parameters: tool_name(param1=\"value1\", param2=\"value2\")"
            )

        tool = self._tools.get(name)
        if not tool:
            return None, params, (
                f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            )
        return tool, cast_params, None

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """按名称与给定参数执行工具。"""
        _HINT = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return error + _HINT

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + _HINT

    def subset(self, names: list[str]) -> tuple["ToolRegistry", list[str]]:
        """按名称复制子集；返回 (新注册表, 未找到的工具名)。"""
        registry = ToolRegistry()
        missing: list[str] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                missing.append(name)
            else:
                registry.register(tool)
        return registry, missing

    @property
    def tool_names(self) -> list[str]:
        """获取已注册工具名称列表。"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
