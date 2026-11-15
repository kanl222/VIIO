"""User-visible task steps; never stores hidden model reasoning."""
import json
from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    status: Literal['pending', 'in_progress', 'completed'] = 'pending'


@tool('manage_plan', response_format='content_and_artifact')
def manage_plan(steps: Annotated[list[PlanStep], Field(min_length=1, max_length=12)]) -> tuple[str, dict | None]:
    """Create/update a concise task plan for a multi-step request.
    Submit the full list each time. Use pending, in_progress, completed.
    Keep at most one step in progress. Mark completed only after actual execution/verification.
    Describe actions and outcomes, never hidden reasoning. Simple questions need no plan.
    """
    if sum(step.status == 'in_progress' for step in steps) > 1:
        return 'Одновременно выполняйте только один шаг плана.', None
    plan = [step.model_dump() for step in steps]
    return json.dumps(plan, ensure_ascii=False), {'plan': plan}
