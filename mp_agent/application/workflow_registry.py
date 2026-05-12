from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from mp_agent.application.competitor_workflows import (
    AMAZON_WORKFLOW_SCHEMA,
    EBAY_WORKFLOW_SCHEMA,
    run_amazon_competitor_analysis,
    run_ebay_competitor_analysis,
)


WorkflowHandler = Callable[..., Awaitable[dict]]


@dataclass
class WorkflowTool:
    name: str
    schema: dict
    handler: WorkflowHandler


class WorkflowRegistry:
    def __init__(self):
        self._tools: dict[str, WorkflowTool] = {}

    def register(self, tool: WorkflowTool) -> None:
        self._tools[tool.name] = tool

    def get_tool_schemas(self) -> list[dict]:
        return [tool.schema for tool in self._tools.values()]

    async def call_tool(self, name: str, arguments: dict, emit) -> dict:
        tool = self._tools[name]
        return await tool.handler(emit=emit, **arguments)


def build_default_registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(
        WorkflowTool(
            name="run_amazon_competitor_analysis",
            schema=AMAZON_WORKFLOW_SCHEMA,
            handler=run_amazon_competitor_analysis,
        )
    )
    registry.register(
        WorkflowTool(
            name="run_ebay_competitor_analysis",
            schema=EBAY_WORKFLOW_SCHEMA,
            handler=run_ebay_competitor_analysis,
        )
    )
    return registry
