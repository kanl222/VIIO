"""Coding model selection; tool binding belongs to neither this factory nor coder."""

from langchain_core.language_models.chat_models import BaseChatModel

from assistent.config import Settings
from assistent.models.chat import create_chat_model


def create_coding_model(settings: Settings) -> BaseChatModel:
    """Use coder sampling settings even when the model name is shared."""
    return create_chat_model(settings, model_name=settings.coding_model, coding=True)
