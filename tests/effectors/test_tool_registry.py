"""TC-6a.1 — the ``Tool`` contract + the dynamic ``ToolRegistry`` (the Effector belt).

Pure, no I/O: the registry only stores + resolves tools (running them is the
dispatch's job, FC-5). We prove the belt is runtime-mutable (register / retire),
that a tool declares its own hazard class, that valid args produce a ``ToolResult``,
and — the safety-relevant part — that bad args raise a *typed* ``ValidationError``
rather than reaching the tool body as an untyped crash.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from brain.effectors.tools import (
    NOOP_TOOL_NAME,
    DangerClass,
    NoopArgs,
    NoopTool,
    ToolRegistry,
    ToolResult,
    default_tool_registry,
    validate_args,
)


def test_register_and_resolve_by_name() -> None:
    registry = ToolRegistry()
    tool = NoopTool()

    registry.register(tool)

    assert NOOP_TOOL_NAME in registry
    assert registry.resolve(NOOP_TOOL_NAME) is tool
    assert registry.get(NOOP_TOOL_NAME) is tool
    assert registry.names() == [NOOP_TOOL_NAME]


def test_default_registry_ships_the_inert_noop_tool() -> None:
    registry = default_tool_registry()

    assert registry.names() == [NOOP_TOOL_NAME]
    tool = registry.resolve(NOOP_TOOL_NAME)
    # The tool declares its own hazard class — the Conscience/Core read it, never infer.
    assert tool.danger is DangerClass.SAFE


def test_registering_a_duplicate_name_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(NoopTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(NoopTool())


def test_retire_makes_a_tool_unresolvable() -> None:
    registry = default_tool_registry()
    assert NOOP_TOOL_NAME in registry

    registry.retire(NOOP_TOOL_NAME)

    assert NOOP_TOOL_NAME not in registry
    assert registry.get(NOOP_TOOL_NAME) is None
    with pytest.raises(KeyError):
        registry.resolve(NOOP_TOOL_NAME)


def test_retiring_an_absent_tool_is_a_noop() -> None:
    registry = ToolRegistry()
    registry.retire("never-registered")  # must not raise
    assert registry.names() == []


async def test_valid_args_run_to_a_tool_result() -> None:
    tool = NoopTool()
    validated = validate_args(tool, {"message": "hello"})

    result = await tool.run(validated)  # type: ignore[arg-type]

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.output == {"echo": "hello"}


@pytest.mark.parametrize(
    "bad_args",
    [
        pytest.param({}, id="missing-required-message"),
        pytest.param({"message": 123}, id="wrong-type"),
        pytest.param({"message": "hi", "extra": "nope"}, id="unknown-field-forbidden"),
    ],
)
def test_bad_args_raise_typed_validation_error(bad_args: dict[str, object]) -> None:
    tool = NoopTool()
    # The single place arg-shape is enforced: a typed pydantic error, never an
    # untyped crash inside the tool body.
    with pytest.raises(ValidationError):
        validate_args(tool, bad_args)


def test_noop_args_schema_is_strict() -> None:
    # extra="forbid" is what makes the unknown-field case above a hard error.
    assert NoopArgs.model_config.get("extra") == "forbid"
