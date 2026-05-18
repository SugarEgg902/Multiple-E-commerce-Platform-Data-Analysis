from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from mp_agent.application.competitor_workflows import (
    AMAZON_WORKFLOW_SCHEMA,
    EBAY_WORKFLOW_SCHEMA,
    TEMU_WORKFLOW_SCHEMA,
    OZON_WORKFLOW_SCHEMA,
    OTTO_WORKFLOW_SCHEMA,
    ALLEGRO_WORKFLOW_SCHEMA,
    TIKTOKSHOP_WORKFLOW_SCHEMA,
    CDISCOUNT_WORKFLOW_SCHEMA,
    ALIEXPRESS_WORKFLOW_SCHEMA,
    run_amazon_competitor_analysis,
    run_ebay_competitor_analysis,
    run_temu_competitor_analysis,
    run_ozon_competitor_analysis,
    run_otto_competitor_analysis,
    run_allegro_competitor_analysis,
    run_tiktokshop_competitor_analysis,
    run_cdiscount_competitor_analysis,
    run_aliexpress_competitor_analysis,
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
    registry.register(
        WorkflowTool(
            name="run_temu_competitor_analysis",
            schema=TEMU_WORKFLOW_SCHEMA,
            handler=run_temu_competitor_analysis,
        )
    )
    registry.register(
        WorkflowTool(
            name="run_ozon_competitor_analysis",
            schema=OZON_WORKFLOW_SCHEMA,
            handler=run_ozon_competitor_analysis,
        )
    )
    registry.register(
        WorkflowTool(
            name="run_otto_competitor_analysis",
            schema=OTTO_WORKFLOW_SCHEMA,
            handler=run_otto_competitor_analysis,
        )
    )
    registry.register(
        WorkflowTool(
            name="run_allegro_competitor_analysis",
            schema=ALLEGRO_WORKFLOW_SCHEMA,
            handler=run_allegro_competitor_analysis,
        )
    )
    registry.register(
        WorkflowTool(
            name="run_tiktokshop_competitor_analysis",
            schema=TIKTOKSHOP_WORKFLOW_SCHEMA,
            handler=run_tiktokshop_competitor_analysis,
        )
    )
    registry.register(
        WorkflowTool(
            name="run_cdiscount_competitor_analysis",
            schema=CDISCOUNT_WORKFLOW_SCHEMA,
            handler=run_cdiscount_competitor_analysis,
        )
    )
    registry.register(
        WorkflowTool(
            name="run_aliexpress_competitor_analysis",
            schema=ALIEXPRESS_WORKFLOW_SCHEMA,
            handler=run_aliexpress_competitor_analysis,
        )
    )
    return registry
