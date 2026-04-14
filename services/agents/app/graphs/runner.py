"""Lightweight async graph runner — drop-in replacement for LangGraph's StateGraph."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

END = "__end__"


class Graph:
    """Minimal async graph runner.

    Supports named nodes (async callables), linear edges, conditional edges
    (routing functions), entry point, and an ``END`` sentinel.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._edges: dict[str, str] = {}
        self._cond_edges: dict[str, tuple[Callable[..., str], dict[str, str]]] = {}
        self._entry: str | None = None

    def add_node(self, name: str, fn: Callable[..., Awaitable[Any]]) -> None:
        self._nodes[name] = fn

    def add_edge(self, src: str, dst: str) -> None:
        self._edges[src] = dst

    def add_conditional_edges(
        self,
        src: str,
        router_fn: Callable[..., str],
        mapping: dict[str, str],
    ) -> None:
        self._cond_edges[src] = (router_fn, mapping)

    def set_entry_point(self, name: str) -> None:
        self._entry = name

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute the graph starting from the entry point."""
        if self._entry is None:
            raise RuntimeError("No entry point set on graph")

        current = self._entry
        while current != END:
            state = await self._nodes[current](state)
            if current in self._cond_edges:
                router_fn, mapping = self._cond_edges[current]
                route_key = router_fn(state)
                current = mapping[route_key]
            elif current in self._edges:
                current = self._edges[current]
            else:
                break
        return state
