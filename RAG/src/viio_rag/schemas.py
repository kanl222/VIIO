import uuid
from pydantic import BaseModel, ConfigDict, Field, StrictBool


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
