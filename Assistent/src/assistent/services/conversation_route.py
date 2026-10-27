"""Checkpoint only the active specialist, never model prompts or references."""

from __future__ import annotations

import hashlib
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class RouteState(TypedDict):
    active: str


class ConversationRoute:
    def __init__(self, checkpointer):
        graph = StateGraph(RouteState)
        graph.add_node("remember", lambda state: {"active": state["active"]})
        graph.add_edge(START, "remember")
        graph.add_edge("remember", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    @staticmethod
    def config(thread_id, user_context):
        identity = json.dumps([thread_id, (user_context or {}).get("user_id")], ensure_ascii=False)
        return {"configurable": {"thread_id": "route-text-" + hashlib.sha256(identity.encode()).hexdigest()}}

    async def get(self, thread_id, user_context):
        if not self.graph.checkpointer:
            return None
        snapshot = await self.graph.aget_state(self.config(thread_id, user_context))
        return snapshot.values.get("active")

    async def set(self, active, thread_id, user_context):
        if self.graph.checkpointer:
            await self.graph.ainvoke({"active": active}, self.config(thread_id, user_context))
