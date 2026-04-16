"""nanobot 的交互式初始化（onboarding）问卷向导。"""

import json
import types
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, NamedTuple, get_args, get_origin

try:
    import questionary
except ModuleNotFoundError:  # pragma: no cover - 在没有向导依赖的环境下会走到这里
    questionary = None
from loguru import logger
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nanobot.cli.models import (
    format_token_count,
    get_model_context_limit,
    get_model_suggestions,
)
from nanobot.config.loader import get_config_path, load_config
from nanobot.config.schema import Config

console = Console()


@dataclass
class OnboardResult:
    """一次 onboarding 会话的结果。"""

    config: Config
    should_save: bool

# --- 选择字段的提示信息 ---
# 把字段名映射到 (choices, hint_text)
# 如需新增带提示的选择字段，在此添加一项，例如：
#   "field_name": (["choice1", "choice2", ...], "该字段的提示文本")
_SELECT_FIELD_HINTS: dict[str, tuple[list[str], str]] = {
    "reasoning_effort": (
        ["low", "medium", "high"],
        "low / medium / high - 启用 LLM 思考模式",
    ),
}

# --- 导航按键绑定 ---

_BACK_PRESSED = object()  # 用于“返回上一步”的哨兵值


def _get_questionary():
    """返回 questionary；若向导依赖不可用，则抛出清晰错误。"""
    if questionary is None:
        raise RuntimeError(
            "Interactive onboarding requires the optional 'questionary' dependency. "
            "Install project dependencies and rerun with --wizard."
        )
    return questionary


def _select_with_back(
    prompt: str, choices: list[str], default: str | None = None
) -> str | None | object:
    """选择器：支持按 Escape/Left 返回上一步。

    Args:
        prompt: 要显示的提示文本。
        choices: 可选项列表。不能为空。
        default: 默认选中的选项；若不在 choices 中则使用第一个。

    Returns:
        若用户按 Escape 或 Left，则返回 _BACK_PRESSED 哨兵值；
        若用户确认选择，则返回选中的字符串；
        若用户取消（Ctrl+C），则返回 None。
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    # 校验可选项
    if not choices:
        logger.warning("Empty choices list provided to _select_with_back")
        return None

    # 计算默认选中项索引
    selected_index = 0
    if default and default in choices:
        selected_index = choices.index(default)

    # 结果状态容器
    state: dict[str, str | None | object] = {"result": None}

    # 构造菜单项（通过闭包读取 selected_index）
    def get_menu_text():
        items = []
        for i, choice in enumerate(choices):
            if i == selected_index:
                items.append(("class:selected", f"> {choice}\n"))
            else:
                items.append(("", f"  {choice}\n"))
        return items

    # 创建布局
    menu_control = FormattedTextControl(get_menu_text)
    menu_window = Window(content=menu_control, height=len(choices))

    prompt_control = FormattedTextControl(lambda: [("class:question", f"> {prompt}")])
    prompt_window = Window(content=prompt_control, height=1)

    layout = Layout(HSplit([prompt_window, menu_window]))

    # 按键绑定
    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def _up(event):
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(choices)
        event.app.invalidate()

    @bindings.add(Keys.Down)
    def _down(event):
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(choices)
        event.app.invalidate()

    @bindings.add(Keys.Enter)
    def _enter(event):
        state["result"] = choices[selected_index]
        event.app.exit()

    @bindings.add("escape")
    def _escape(event):
        state["result"] = _BACK_PRESSED
        event.app.exit()

    @bindings.add(Keys.Left)
    def _left(event):
        state["result"] = _BACK_PRESSED
        event.app.exit()

    @bindings.add(Keys.ControlC)
    def _ctrl_c(event):
        state["result"] = None
        event.app.exit()

    # 样式
    style = Style.from_dict({
        "selected": "fg:green bold",
        "question": "fg:cyan",
    })

    app = Application(layout=layout, key_bindings=bindings, style=style)
    try:
        app.run()
    except Exception:
        logger.exception("Error in select prompt")
        return None

    return state["result"]

# --- 类型推断（Type Introspection） ---


class FieldTypeInfo(NamedTuple):
    """字段类型推断的结果。"""

    type_name: str
    inner_type: Any


def _get_field_type_info(field_info) -> FieldTypeInfo:
    """从 Pydantic 字段中提取类型信息。"""
    annotation = field_info.annotation
    if annotation is None:
        return FieldTypeInfo("str", None)

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is types.UnionType:
        non_none_args = [a for a in args if a is not type(None)]
        if len(non_none_args) == 1:
            annotation = non_none_args[0]
            origin = get_origin(annotation)
            args = get_args(annotation)

    _SIMPLE_TYPES: dict[type, str] = {bool: "bool", int: "int", float: "float"}

    if origin is list or (hasattr(origin, "__name__") and origin.__name__ == "List"):
        return FieldTypeInfo("list", args[0] if args else str)
    if origin is dict or (hasattr(origin, "__name__") and origin.__name__ == "Dict"):
        return FieldTypeInfo("dict", None)
    for py_type, name in _SIMPLE_TYPES.items():
        if annotation is py_type:
            return FieldTypeInfo(name, None)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return FieldTypeInfo("model", annotation)
    return FieldTypeInfo("str", None)


def _get_field_display_name(field_key: str, field_info) -> str:
    """获取字段的展示名称。"""
    if field_info and field_info.description:
        return field_info.description
    name = field_key
    suffix_map = {
        "_s": " (seconds)",
        "_ms": " (ms)",
        "_url": " URL",
        "_path": " Path",
        "_id": " ID",
        "_key": " Key",
        "_token": " Token",
    }
    for suffix, replacement in suffix_map.items():
        if name.endswith(suffix):
            name = name[: -len(suffix)] + replacement
            break
    return name.replace("_", " ").title()


# --- 敏感字段脱敏 ---

_SENSITIVE_KEYWORDS = frozenset({"api_key", "token", "secret", "password", "credentials"})


def _is_sensitive_field(field_name: str) -> bool:
    """判断字段名是否暗示其包含敏感内容。"""
    return any(kw in field_name.lower() for kw in _SENSITIVE_KEYWORDS)


def _mask_value(value: str) -> str:
    """对敏感值做脱敏处理，仅展示最后 4 个字符。"""
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


# --- 值格式化 ---


def _format_value(value: Any, rich: bool = True, field_name: str = "") -> str:
    """用于安全展示值的递归入口，可处理任意深度的嵌套结构。"""
    if value is None or value == "" or value == {} or value == []:
        return "[dim]not set[/dim]" if rich else "[not set]"
    if _is_sensitive_field(field_name) and isinstance(value, str):
        masked = _mask_value(value)
        return f"[dim]{masked}[/dim]" if rich else masked
    if isinstance(value, BaseModel):
        parts = []
        for fname, _finfo in type(value).model_fields.items():
            fval = getattr(value, fname, None)
            formatted = _format_value(fval, rich=False, field_name=fname)
            if formatted != "[not set]":
                parts.append(f"{fname}={formatted}")
        return ", ".join(parts) if parts else ("[dim]not set[/dim]" if rich else "[not set]")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


def _format_value_for_input(value: Any, field_type: str) -> str:
    """把值格式化为输入框的默认值字符串。"""
    if value is None or value == "":
        return ""
    if field_type == "list" and isinstance(value, list):
        return ",".join(str(v) for v in value)
    if field_type == "dict" and isinstance(value, dict):
        return json.dumps(value)
    return str(value)


# --- Rich UI 组件 ---


def _show_config_panel(display_name: str, model: BaseModel, fields: list) -> None:
    """以 rich 表格展示当前配置。"""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    for fname, field_info in fields:
        value = getattr(model, fname, None)
        display = _get_field_display_name(fname, field_info)
        formatted = _format_value(value, rich=True, field_name=fname)
        table.add_row(display, formatted)

    console.print(Panel(table, title=f"[bold]{display_name}[/bold]", border_style="blue"))


def _show_main_menu_header() -> None:
    """显示主菜单头部。"""
    from nanobot import __logo__, __version__

    console.print()
    # 单行标题使用 Align.CENTER 居中
    from rich.align import Align

    console.print(
        Align.center(f"{__logo__} [bold cyan]nanobot[{__version__}][/bold cyan]")
    )
    console.print()


def _show_section_header(title: str, subtitle: str = "") -> None:
    """显示分区标题。"""
    console.print()
    if subtitle:
        console.print(
            Panel(f"[dim]{subtitle}[/dim]", title=f"[bold]{title}[/bold]", border_style="blue")
        )
    else:
        console.print(Panel("", title=f"[bold]{title}[/bold]", border_style="blue"))


# --- 输入处理 ---


def _input_bool(display_name: str, current: bool | None) -> bool | None:
    """通过确认框获取布尔值输入。"""
    return _get_questionary().confirm(
        display_name,
        default=bool(current) if current is not None else False,
    ).ask()


def _input_text(display_name: str, current: Any, field_type: str) -> Any:
    """获取文本输入，并按字段类型解析。"""
    default = _format_value_for_input(current, field_type)

    value = _get_questionary().text(f"{display_name}:", default=default).ask()

    if value is None or value == "":
        return None

    if field_type == "int":
        try:
            return int(value)
        except ValueError:
            console.print("[yellow]! Invalid number format, value not saved[/yellow]")
            return None
    elif field_type == "float":
        try:
            return float(value)
        except ValueError:
            console.print("[yellow]! Invalid number format, value not saved[/yellow]")
            return None
    elif field_type == "list":
        return [v.strip() for v in value.split(",") if v.strip()]
    elif field_type == "dict":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            console.print("[yellow]! Invalid JSON format, value not saved[/yellow]")
            return None

    return value


def _input_with_existing(
    display_name: str, current: Any, field_type: str
) -> Any:
    """对非空值提供“保留现有值”的输入处理。"""
    has_existing = current is not None and current != "" and current != {} and current != []

    if has_existing and not isinstance(current, list):
        choice = _get_questionary().select(
            display_name,
            choices=["Enter new value", "Keep existing value"],
            default="Keep existing value",
        ).ask()
        if choice == "Keep existing value" or choice is None:
            return None

    return _input_text(display_name, current, field_type)


# --- Pydantic Model Configuration ---


def _get_current_provider(model: BaseModel) -> str:
    """从 model 中读取当前 provider 设置（如果存在）。"""
    if hasattr(model, "provider"):
        return getattr(model, "provider", "auto") or "auto"
    return "auto"


def _input_model_with_autocomplete(
    display_name: str, current: Any, provider: str
) -> str | None:
    """获取模型名输入，并提供自动补全建议。"""
    from prompt_toolkit.completion import Completer, Completion

    default = str(current) if current else ""

    class DynamicModelCompleter(Completer):
        """动态获取模型建议的自动补全器。"""

        def __init__(self, provider_name: str):
            self.provider = provider_name

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            suggestions = get_model_suggestions(text, provider=self.provider, limit=50)
            for model in suggestions:
                # 若模型名不包含已输入内容则跳过
                if text.lower() not in model.lower():
                    continue
                yield Completion(
                    model,
                    start_position=-len(text),
                    display=model,
                )

    value = _get_questionary().autocomplete(
        f"{display_name}:",
        choices=[""],  # 占位：实际补全由 completer 提供
        completer=DynamicModelCompleter(provider),
        default=default,
        qmark=">",
    ).ask()

    return value if value else None


def _input_context_window_with_recommendation(
    display_name: str, current: Any, model_obj: BaseModel
) -> int | None:
    """获取上下文窗口大小输入，并提供“获取推荐值”的选项。"""
    current_val = current if current else ""

    choices = ["Enter new value"]
    if current_val:
        choices.append("Keep existing value")
    choices.append("[?] 获取推荐值")

    choice = _get_questionary().select(
        display_name,
        choices=choices,
        default="Enter new value",
    ).ask()

    if choice is None:
        return None

    if choice == "Keep existing value":
        return None

    if choice == "[?] 获取推荐值":
        # 从 model 对象中取出模型名
        model_name = getattr(model_obj, "model", None)
        if not model_name:
            console.print("[yellow]! Please configure the model field first[/yellow]")
            return None

        provider = _get_current_provider(model_obj)
        context_limit = get_model_context_limit(model_name, provider)

        if context_limit:
            console.print(f"[green]+ Recommended context window: {format_token_count(context_limit)} tokens[/green]")
            return context_limit
        else:
            console.print("[yellow]! Could not fetch model info, please enter manually[/yellow]")
            # 回落到手动输入

    # 手动输入
    value = _get_questionary().text(
        f"{display_name}:",
        default=str(current_val) if current_val else "",
    ).ask()

    if value is None or value == "":
        return None

    try:
        return int(value)
    except ValueError:
        console.print("[yellow]! Invalid number format, value not saved[/yellow]")
        return None


def _handle_model_field(
    working_model: BaseModel, field_name: str, field_display: str, current_value: Any
) -> None:
    """处理 'model' 字段：提供自动补全，并尝试自动填充 context window。"""
    provider = _get_current_provider(working_model)
    new_value = _input_model_with_autocomplete(field_display, current_value, provider)
    if new_value is not None and new_value != current_value:
        setattr(working_model, field_name, new_value)
        _try_auto_fill_context_window(working_model, new_value)


def _handle_context_window_field(
    working_model: BaseModel, field_name: str, field_display: str, current_value: Any
) -> None:
    """处理 context_window_tokens：支持查询推荐值。"""
    new_value = _input_context_window_with_recommendation(
        field_display, current_value, working_model
    )
    if new_value is not None:
        setattr(working_model, field_name, new_value)


_FIELD_HANDLERS: dict[str, Any] = {
    "model": _handle_model_field,
    "context_window_tokens": _handle_context_window_field,
}


def _configure_pydantic_model(
    model: BaseModel,
    display_name: str,
    *,
    skip_fields: set[str] | None = None,
) -> BaseModel | None:
    """交互式配置一个 Pydantic model。

    只有当用户明确选择 “Done” 时，才会返回更新后的 model。
    返回（Back）与取消（Cancel）动作会丢弃本节的草稿修改。
    """
    skip_fields = skip_fields or set()
    working_model = model.model_copy(deep=True)

    fields = [
        (name, info)
        for name, info in type(working_model).model_fields.items()
        if name not in skip_fields
    ]
    if not fields:
        console.print(f"[dim]{display_name}: No configurable fields[/dim]")
        return working_model

    def get_choices() -> list[str]:
        items = []
        for fname, finfo in fields:
            value = getattr(working_model, fname, None)
            display = _get_field_display_name(fname, finfo)
            formatted = _format_value(value, rich=False, field_name=fname)
            items.append(f"{display}: {formatted}")
        return items + ["[Done]"]

    while True:
        console.clear()
        _show_config_panel(display_name, working_model, fields)
        choices = get_choices()
        answer = _select_with_back("Select field to configure:", choices)

        if answer is _BACK_PRESSED or answer is None:
            return None
        if answer == "[Done]":
            return working_model

        field_idx = next((i for i, c in enumerate(choices) if c == answer), -1)
        if field_idx < 0 or field_idx >= len(fields):
            return None

        field_name, field_info = fields[field_idx]
        current_value = getattr(working_model, field_name, None)
        ftype = _get_field_type_info(field_info)
        field_display = _get_field_display_name(field_name, field_info)

        # 嵌套的 Pydantic model：递归配置
        if ftype.type_name == "model":
            nested = current_value
            created = nested is None
            if nested is None and ftype.inner_type:
                nested = ftype.inner_type()
            if nested and isinstance(nested, BaseModel):
                updated = _configure_pydantic_model(nested, field_display)
                if updated is not None:
                    setattr(working_model, field_name, updated)
                elif created:
                    setattr(working_model, field_name, None)
            continue

        # 已注册的特殊字段处理器
        handler = _FIELD_HANDLERS.get(field_name)
        if handler:
            handler(working_model, field_name, field_display, current_value)
            continue

        # 带提示的选择字段（例如 reasoning_effort）
        if field_name in _SELECT_FIELD_HINTS:
            choices_list, hint = _SELECT_FIELD_HINTS[field_name]
            select_choices = choices_list + ["(clear/unset)"]
            console.print(f"[dim]  提示：{hint}[/dim]")
            new_value = _select_with_back(
                field_display, select_choices, default=current_value or select_choices[0]
            )
            if new_value is _BACK_PRESSED:
                continue
            if new_value == "(clear/unset)":
                setattr(working_model, field_name, None)
            elif new_value is not None:
                setattr(working_model, field_name, new_value)
            continue

        # 通用字段输入
        if ftype.type_name == "bool":
            new_value = _input_bool(field_display, current_value)
        else:
            new_value = _input_with_existing(field_display, current_value, ftype.type_name)
        if new_value is not None:
            setattr(working_model, field_name, new_value)


def _try_auto_fill_context_window(model: BaseModel, new_model_name: str) -> None:
    """当 context_window_tokens 仍为默认值时，尝试自动填充推荐值。

    Note:
        该函数会从 nanobot.config.schema 导入 AgentDefaults，以获取默认的
        context_window_tokens 值。如果 schema 发生变化，这里的耦合也需要同步更新。
    """
    # 检查是否存在 context_window_tokens 字段
    if not hasattr(model, "context_window_tokens"):
        return

    current_context = getattr(model, "context_window_tokens", None)

    # 检查当前值是否为默认值（例如 65536）
    # 仅当用户尚未修改默认值时才自动填充
    from nanobot.config.schema import AgentDefaults

    default_context = AgentDefaults.model_fields["context_window_tokens"].default

    if current_context != default_context:
        return  # 用户已自定义，不覆盖

    provider = _get_current_provider(model)
    context_limit = get_model_context_limit(new_model_name, provider)

    if context_limit:
        setattr(model, "context_window_tokens", context_limit)
        console.print(f"[green]+ Auto-filled context window: {format_token_count(context_limit)} tokens[/green]")
    else:
        console.print("[dim](i) Could not auto-fill context window (model not in database)[/dim]")


# --- Provider 配置 ---


@lru_cache(maxsize=1)
def _get_provider_info() -> dict[str, tuple[str, bool, bool, str]]:
    """从 registry 获取 provider 信息（带缓存）。"""
    from nanobot.providers.registry import PROVIDERS

    return {
        spec.name: (
            spec.display_name or spec.name,
            spec.is_gateway,
            spec.is_local,
            spec.default_api_base,
        )
        for spec in PROVIDERS
        if not spec.is_oauth
    }


def _get_provider_names() -> dict[str, str]:
    """获取 provider 的展示名称。"""
    info = _get_provider_info()
    return {name: data[0] for name, data in info.items() if name}


def _configure_provider(config: Config, provider_name: str) -> None:
    """配置单个 LLM provider。"""
    provider_config = getattr(config.providers, provider_name, None)
    if provider_config is None:
        console.print(f"[red]Unknown provider: {provider_name}[/red]")
        return

    display_name = _get_provider_names().get(provider_name, provider_name)
    info = _get_provider_info()
    default_api_base = info.get(provider_name, (None, None, None, None))[3]

    if default_api_base and not provider_config.api_base:
        provider_config.api_base = default_api_base

    updated_provider = _configure_pydantic_model(
        provider_config,
        display_name,
    )
    if updated_provider is not None:
        setattr(config.providers, provider_name, updated_provider)


def _configure_providers(config: Config) -> None:
    """配置 LLM providers。"""

    def get_provider_choices() -> list[str]:
        """构造 provider 选项，并用标记展示配置状态。"""
        choices = []
        for name, display in _get_provider_names().items():
            provider = getattr(config.providers, name, None)
            if provider and provider.api_key:
                choices.append(f"{display} *")
            else:
                choices.append(display)
        return choices + ["<- Back"]

    while True:
        try:
            console.clear()
            _show_section_header("LLM Providers", "选择一个 provider 来配置 API key 与 endpoint")
            choices = get_provider_choices()
            answer = _select_with_back("Select provider:", choices)

            if answer is _BACK_PRESSED or answer is None or answer == "<- Back":
                break

            # 类型保护：此时 answer 保证为字符串
            assert isinstance(answer, str)
            # 从选项中提取 provider 名（如有 " *" 后缀则移除）
            provider_name = answer.replace(" *", "")
            # 通过展示名找到实际的 provider key
            for name, display in _get_provider_names().items():
                if display == provider_name:
                    _configure_provider(config, name)
                    break

        except KeyboardInterrupt:
            console.print("\n[dim]Returning to main menu...[/dim]")
            break


# --- Channel 配置 ---


@lru_cache(maxsize=1)
def _get_channel_info() -> dict[str, tuple[str, type[BaseModel]]]:
    """从 channel 模块获取信息（展示名 + 配置类）。"""
    import importlib

    from nanobot.channels.registry import discover_all

    result: dict[str, tuple[str, type[BaseModel]]] = {}
    for name, channel_cls in discover_all().items():
        try:
            mod = importlib.import_module(f"nanobot.channels.{name}")
            config_name = channel_cls.__name__.replace("Channel", "Config")
            config_cls = getattr(mod, config_name, None)
            if config_cls and isinstance(config_cls, type) and issubclass(config_cls, BaseModel):
                display_name = getattr(channel_cls, "display_name", name.capitalize())
                result[name] = (display_name, config_cls)
        except Exception:
            logger.warning(f"Failed to load channel module: {name}")
    return result


def _get_channel_names() -> dict[str, str]:
    """获取 channel 展示名。"""
    return {name: info[0] for name, info in _get_channel_info().items()}


def _get_channel_config_class(channel: str) -> type[BaseModel] | None:
    """获取 channel 的配置类。"""
    entry = _get_channel_info().get(channel)
    return entry[1] if entry else None


def _configure_channel(config: Config, channel_name: str) -> None:
    """配置单个 channel。"""
    channel_dict = getattr(config.channels, channel_name, None)
    if channel_dict is None:
        channel_dict = {}
        setattr(config.channels, channel_name, channel_dict)

    display_name = _get_channel_names().get(channel_name, channel_name)
    config_cls = _get_channel_config_class(channel_name)

    if config_cls is None:
        console.print(f"[red]No configuration class found for {display_name}[/red]")
        return

    model = config_cls.model_validate(channel_dict) if channel_dict else config_cls()

    updated_channel = _configure_pydantic_model(
        model,
        display_name,
    )
    if updated_channel is not None:
        new_dict = updated_channel.model_dump(by_alias=True, exclude_none=True)
        setattr(config.channels, channel_name, new_dict)


def _configure_channels(config: Config) -> None:
    """配置聊天 channels。"""
    channel_names = list(_get_channel_names().keys())
    choices = channel_names + ["<- Back"]

    while True:
        try:
            console.clear()
            _show_section_header("Chat Channels", "选择一个 channel 来配置连接设置")
            answer = _select_with_back("Select channel:", choices)

            if answer is _BACK_PRESSED or answer is None or answer == "<- Back":
                break

            # 类型保护：此时 answer 保证为字符串
            assert isinstance(answer, str)
            _configure_channel(config, answer)
        except KeyboardInterrupt:
            console.print("\n[dim]Returning to main menu...[/dim]")
            break


# --- 通用设置 ---

_SETTINGS_SECTIONS: dict[str, tuple[str, str, set[str] | None]] = {
    "Agent Settings": ("Agent Defaults", "配置默认模型、temperature 与行为", None),
    "Gateway": ("Gateway Settings", "配置服务 host、port 与 heartbeat", None),
    "Tools": ("Tools Settings", "配置 web 搜索、shell 执行与其他工具", {"mcp_servers"}),
}

_SETTINGS_GETTER = {
    "Agent Settings": lambda c: c.agents.defaults,
    "Gateway": lambda c: c.gateway,
    "Tools": lambda c: c.tools,
}

_SETTINGS_SETTER = {
    "Agent Settings": lambda c, v: setattr(c.agents, "defaults", v),
    "Gateway": lambda c, v: setattr(c, "gateway", v),
    "Tools": lambda c, v: setattr(c, "tools", v),
}


def _configure_general_settings(config: Config, section: str) -> None:
    """配置一个通用设置分区（标题 + model 编辑 + 回写）。"""
    meta = _SETTINGS_SECTIONS.get(section)
    if not meta:
        return
    display_name, subtitle, skip = meta
    model = _SETTINGS_GETTER[section](config)
    updated = _configure_pydantic_model(model, display_name, skip_fields=skip)
    if updated is not None:
        _SETTINGS_SETTER[section](config, updated)


# --- Summary ---


def _summarize_model(obj: BaseModel) -> list[tuple[str, str]]:
    """递归汇总一个 Pydantic model，返回 (field, value) 列表。"""
    items: list[tuple[str, str]] = []
    for field_name, field_info in type(obj).model_fields.items():
        value = getattr(obj, field_name, None)
        if value is None or value == "" or value == {} or value == []:
            continue
        display = _get_field_display_name(field_name, field_info)
        ftype = _get_field_type_info(field_info)
        if ftype.type_name == "model" and isinstance(value, BaseModel):
            for nested_field, nested_value in _summarize_model(value):
                items.append((f"{display}.{nested_field}", nested_value))
            continue
        formatted = _format_value(value, rich=False, field_name=field_name)
        if formatted != "[not set]":
            items.append((display, formatted))
    return items


def _print_summary_panel(rows: list[tuple[str, str]], title: str) -> None:
    """构造并打印两列表格的汇总面板。"""
    if not rows:
        return
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Setting", style="cyan")
    table.add_column("Value")
    for field, value in rows:
        table.add_row(field, value)
    console.print(Panel(table, title=f"[bold]{title}[/bold]", border_style="blue"))


def _show_summary(config: Config) -> None:
    """使用 rich 展示配置汇总。"""
    console.print()

    # Providers（LLM 提供方）
    provider_rows = []
    for name, display in _get_provider_names().items():
        provider = getattr(config.providers, name, None)
        status = "[green]configured[/green]" if (provider and provider.api_key) else "[dim]not configured[/dim]"
        provider_rows.append((display, status))
    _print_summary_panel(provider_rows, "LLM Providers")

    # Channels（聊天渠道）
    channel_rows = []
    for name, display in _get_channel_names().items():
        channel = getattr(config.channels, name, None)
        if channel:
            enabled = (
                channel.get("enabled", False)
                if isinstance(channel, dict)
                else getattr(channel, "enabled", False)
            )
            status = "[green]enabled[/green]" if enabled else "[dim]disabled[/dim]"
        else:
            status = "[dim]not configured[/dim]"
        channel_rows.append((display, status))
    _print_summary_panel(channel_rows, "Chat Channels")

    # Settings sections（设置分区）
    for title, model in [
        ("Agent Settings", config.agents.defaults),
        ("Gateway", config.gateway),
        ("Tools", config.tools),
        ("Channel Common", config.channels),
    ]:
        _print_summary_panel(_summarize_model(model), title)


# --- 主入口 ---


def _has_unsaved_changes(original: Config, current: Config) -> bool:
    """当 onboarding 会话产生了变更时返回 True。"""
    return original.model_dump(by_alias=True) != current.model_dump(by_alias=True)


def _prompt_main_menu_exit(has_unsaved_changes: bool) -> str:
    """决定如何退出主菜单。"""
    if not has_unsaved_changes:
        return "discard"

    answer = _get_questionary().select(
        "You have unsaved changes. What would you like to do?",
        choices=[
            "[S] Save and Exit",
            "[X] Exit Without Saving",
            "[R] Resume Editing",
        ],
        default="[R] Resume Editing",
        qmark=">",
    ).ask()

    if answer == "[S] Save and Exit":
        return "save"
    if answer == "[X] Exit Without Saving":
        return "discard"
    return "resume"


def run_onboard(initial_config: Config | None = None) -> OnboardResult:
    """运行交互式 onboarding 向导。

    Args:
        initial_config: 可选的预加载配置，用作起点。
                       若为 None，则从配置文件加载或创建默认配置。
    """
    _get_questionary()

    if initial_config is not None:
        base_config = initial_config.model_copy(deep=True)
    else:
        config_path = get_config_path()
        if config_path.exists():
            base_config = load_config()
        else:
            base_config = Config()

    original_config = base_config.model_copy(deep=True)
    config = base_config.model_copy(deep=True)

    while True:
        console.clear()
        _show_main_menu_header()

        try:
            answer = _get_questionary().select(
                "What would you like to configure?",
                choices=[
                    "[P] LLM Provider",
                    "[C] Chat Channel",
                    "[A] Agent Settings",
                    "[G] Gateway",
                    "[T] Tools",
                    "[V] View Configuration Summary",
                    "[S] Save and Exit",
                    "[X] Exit Without Saving",
                ],
                qmark=">",
            ).ask()
        except KeyboardInterrupt:
            answer = None

        if answer is None:
            action = _prompt_main_menu_exit(_has_unsaved_changes(original_config, config))
            if action == "save":
                return OnboardResult(config=config, should_save=True)
            if action == "discard":
                return OnboardResult(config=original_config, should_save=False)
            continue

        _MENU_DISPATCH = {
            "[P] LLM Provider": lambda: _configure_providers(config),
            "[C] Chat Channel": lambda: _configure_channels(config),
            "[A] Agent Settings": lambda: _configure_general_settings(config, "Agent Settings"),
            "[G] Gateway": lambda: _configure_general_settings(config, "Gateway"),
            "[T] Tools": lambda: _configure_general_settings(config, "Tools"),
            "[V] View Configuration Summary": lambda: _show_summary(config),
        }

        if answer == "[S] Save and Exit":
            return OnboardResult(config=config, should_save=True)
        if answer == "[X] Exit Without Saving":
            return OnboardResult(config=original_config, should_save=False)

        action_fn = _MENU_DISPATCH.get(answer)
        if action_fn:
            action_fn()
