from typing import Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConsultantSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='CONSULTANT_', extra='ignore', hide_input_in_errors=True)
    enabled: bool = False
    rag_backend: Literal['local', 'yandex'] = 'local'
    yandex_enabled: bool = False
    yandex_api_key: str | None = Field(default=None, repr=False)
    yandex_folder_id: str | None = None
    yandex_model: str = 'aliceai-llm'
    # SHA-256 of title + newline + content, approved by an operator for cloud use.
    cloud_public_material_hashes: list[str] = Field(default_factory=list)
    api_key: str | None = None
    source_key: str | None = None
    source_url: str = 'http://documents:8000'
    provider: Literal['lmstudio', 'ollama', 'yandex'] = 'lmstudio'
    model: str | None = None
    lmstudio_base_url: str = 'http://host.docker.internal:1234/v1'
    lmstudio_api_key: str = 'lm-studio'
    lmstudio_model: str = 'qwen/qwen3-4b-2507'
    ollama_base_url: str = 'http://host.docker.internal:11434'
    ollama_model: str = 'llama3.1'
    temperature: float = Field(default=0, ge=0, le=2)
    top_p: float = Field(default=1, gt=0, le=1)
    max_tokens: int = Field(default=2048, ge=128, le=32768)

    @model_validator(mode='after')
    def validate_access(self):
        if self.yandex_enabled or (self.provider == 'yandex' and self.rag_backend == 'local'):
            if not self.api_key or len(self.api_key) < 32:
                raise ValueError('Yandex endpoint requires a consultant API key of at least 32 characters')
            if not self.yandex_api_key or not self.yandex_folder_id:
                raise ValueError('Yandex requires an API key and folder ID')
            if not self.yandex_model.strip():
                raise ValueError('Yandex model must not be empty')
        if self.enabled:
            if not self.api_key or len(self.api_key) < 32 or not self.source_key or len(self.source_key) < 32:
                raise ValueError('Enabled consultant requires separate API and source keys of at least 32 characters')
            if self.api_key == self.source_key:
                raise ValueError('Consultant API and source keys must be different')
        if not self.source_url.startswith(('http://', 'https://')):
            raise ValueError('Source URL must use HTTP or HTTPS')
        return self
