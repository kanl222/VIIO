"""Дата-модель и Pydantic-схемы онтологии Semantic GraphRAG в системе VIIO.

Реализует Property Graph модель предметной области:
    Система -> Меню -> Документы/Инструкции
а также связанные роли, действия (операции) и условия.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# ============================================================================
# 1. Перечисления онтологии (Enums)
# ============================================================================

class NodeLabel(str, Enum):
    """Канонические метки узлов в Memgraph."""
    SYSTEM = "System"
    MENU_PATH = "MenuPath"
    DOCUMENT = "Document"
    CHUNK = "Chunk"
    OPERATION = "Operation"
    ROLE = "Role"
    CONDITION = "Condition"
    DRUG_CATEGORY = "DrugCategory"


class RelationType(str, Enum):
    """Типизированные направленные связи онтологии."""
    HAS_MENU = "HAS_MENU"                     # (:System) -> (:MenuPath)
    DOCUMENTED_BY = "DOCUMENTED_BY"           # (:MenuPath) -> (:Document)
    CONTAINS = "CONTAINS"                     # (:Document) -> (:Chunk)
    CONTAINS_ACTION = "CONTAINS_ACTION"       # (:MenuPath) -> (:Operation)
    EXECUTED_BY = "EXECUTED_BY"               # (:Operation) -> (:Role)
    REQUIRES_CONDITION = "REQUIRES_CONDITION" # (:Operation) -> (:Condition)
    APPLIES_TO = "APPLIES_TO"                 # (:Operation) -> (:DrugCategory)
    NEXT_STEP = "NEXT_STEP"                   # (:Operation) -> (:Operation)


class ConditionSeverity(str, Enum):
    """Уровень критичности бизнес-условия."""
    INFO = "INFO"
    NORMAL = "NORMAL"
    CRITICAL = "CRITICAL"


# ============================================================================
# 2. Модели сущностей онтологии (Graph Nodes)
# ============================================================================

class BaseGraphNode(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SystemNode(BaseGraphNode):
    """Информационная система (например, ИС ЛО)."""
    name: Annotated[str, StringConstraints(min_length=2, max_length=150)]
    code: str = "IS_LO"


class MenuPathNode(BaseGraphNode):
    """Путь меню в интерфейсе программы."""
    full_path: Annotated[str, StringConstraints(min_length=2, max_length=500)]
    section_name: str | None = None

    def get_leaf(self) -> str:
        """Возвращает последний пункт меню из пути."""
        parts = [p.strip() for p in self.full_path.replace("->", ">").split(">")]
        return parts[-1] if parts else self.full_path


class DocumentNode(BaseGraphNode):
    """Исходный нормативный документ или инструкция."""
    id: str
    title: str
    source_url: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChunkNode(BaseGraphNode):
    """Фрагмент текста документа, связанный с вектором в Qdrant."""
    id: str
    doc_id: str
    chunk_index: int = Field(ge=0)
    text: str
    qdrant_point_id: str | None = None


class OperationNode(BaseGraphNode):
    """Бизнес-операция / действие пользователя."""
    name: str
    verb: str | None = None
    description: str | None = None


class RoleNode(BaseGraphNode):
    """Роль или должность исполнителя регламента."""
    code: str
    title: str


class ConditionNode(BaseGraphNode):
    """Ограничивающее правило, исключение или условие."""
    id: str
    rule_text: str
    severity: ConditionSeverity = ConditionSeverity.NORMAL


class DrugCategoryNode(BaseGraphNode):
    """Заглушка (Stub) для категорий лекарственных препаратов."""
    name: str
    is_marked: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 3. Модели связей и доказательной базы (Edges & Provenance)
# ============================================================================

class GraphRelation(BaseModel):
    """Направленная связь с метаданными происхождения."""
    model_config = ConfigDict(extra="ignore")

    source_name: str
    source_label: NodeLabel
    relation_type: RelationType
    target_name: str
    target_label: NodeLabel
    doc_id: str | None = None
    chunk_id: str | None = None
    weight: int = Field(default=1, ge=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    properties: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 4. Модели для LLM-экстракции (Extraction Payload)
# ============================================================================

class ExtractedEntity(BaseModel):
    """Сущность, извлеченная нейросетью из фрагмента текста."""
    model_config = ConfigDict(extra="ignore")

    name: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    label: NodeLabel
    properties: dict[str, Any] = Field(default_factory=dict)


class ExtractedRelation(BaseModel):
    """Связь, извлеченная нейросетью из фрагмента текста."""
    model_config = ConfigDict(extra="ignore")

    source: str
    relation: RelationType
    target: str
    context_quote: str | None = None


class ExtractedGraphPayload(BaseModel):
    """Пакет данных для атомарного сохранения чанка в Memgraph."""
    model_config = ConfigDict(extra="ignore")

    doc_id: str
    chunk_id: str
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


# ============================================================================
# 5. Модели для поискового контекста Semantic RAG (Retrieval Models)
# ============================================================================

class GraphFact(BaseModel):
    """Детерминированный факт онтологии для промпта LLM."""
    model_config = ConfigDict(extra="ignore")

    subject: str
    relation: str
    target: str
    source_doc: str | None = None
    menu_path: str | None = None
    condition: str | None = None
    weight: int = 1

    def to_bullet_point(self) -> str:
        """Человекочитаемое представление факта для промпта."""
        res = f"- {self.subject} [{self.relation}] -> {self.target}"
        if self.menu_path:
            res += f" (Раздел: {self.menu_path})"
        if self.condition:
            res += f" [УСЛОВИЕ: {self.condition}]"
        return res


class GraphRetrievalResult(BaseModel):
    """Результат обхода графа для обогащения контекста в viio_rag."""
    model_config = ConfigDict(extra="ignore")

    anchor_nodes: list[str] = Field(default_factory=list)
    facts: list[GraphFact] = Field(default_factory=list)
    relevant_doc_ids: list[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    fallback_used: bool = False
