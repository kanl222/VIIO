"""Factories for consultant, coding and embedding models."""

from assistent.models.chat import create_chat_model
from assistent.models.coder import create_coding_model
from assistent.models.embeddings import create_embeddings

__all__ = ["create_chat_model", "create_coding_model", "create_embeddings"]
