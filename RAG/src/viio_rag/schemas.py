import uuid
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints


class UserContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str | None = None
    role: str = 'employee'
    departments: list[str] = Field(default_factory=lambda: ['general'])


class KnowledgeQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=4000)
    k: int = Field(default=5, ge=1, le=8)
    user_context: UserContext | None = None
    diagnostics: bool = False
    document_ids: list[str] | None = Field(default=None, min_length=1, max_length=8)


class DocumentListRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(default='', max_length=300)
    tags: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=20, ge=1, le=30)
    offset: int = Field(default=0, ge=0, le=100000)
    user_context: UserContext | None = None


class PublicQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    diagnostics: bool = False


class PublishedIdsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    publication_ids: list[uuid.UUID] = Field(max_length=4)


class ManagedRagQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    confirmed_public_for_external_cloud: StrictBool = False
