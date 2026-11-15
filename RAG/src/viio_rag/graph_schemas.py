"""Дата-модель и Pydantic-схемы онтологии Semantic GraphRAG в системе VIIO.

Реализует Property Graph онтологию для аптечных складов и информационных систем:
- Организация: Organization, Department, Employee, Role
- Процессы и знания: Process, Step, Document, Regulation, Concept, MenuPath, Operation
- Товары и склад: Product, Batch, Pack, Location, StorageCondition, Category, Supplier, Customer, Equipment, Event, Excursion
- Информационные системы: System, Module, Integration, DataEntity, SystemRole, Incident
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
    """Канонические метки узлов в графе Memgraph."""
    # Организационные
    ORGANIZATION = "Organization"
    DEPARTMENT = "Department"
    EMPLOYEE = "Employee"
    ROLE = "Role"

    # Процессы и знания
    PROCESS = "Process"
    STEP = "Step"
    DOCUMENT = "Document"
    REGULATION = "Regulation"
    CONCEPT = "Concept"
    TAG = "Tag"
    CHUNK = "Chunk"
    MENU_PATH = "MenuPath"       # для навигации в интерфейсе
    OPERATION = "Operation"       # совместимость

    # Товары и склад
    PRODUCT = "Product"
    BATCH = "Batch"
    PACK = "Pack"
    LOCATION = "Location"
    STORAGE_CONDITION = "StorageCondition"
    CATEGORY = "Category"
    DRUG_CATEGORY = "DrugCategory" # совместимость
    SUPPLIER = "Supplier"
    CUSTOMER = "Customer"
    EQUIPMENT = "Equipment"
    EVENT = "Event"
    EXCURSION = "Excursion"
    MEASUREMENT = "Measurement"

    # Информационные системы
    SYSTEM = "System"
    MODULE = "Module"
    INTEGRATION = "Integration"
    DATA_ENTITY = "DataEntity"
    SYSTEM_ROLE = "SystemRole"
    INCIDENT = "Incident"
    CONDITION = "Condition"       # совместимость


class RelationType(str, Enum):
    """Типизированные направленные связи онтологии аптечного склада."""
    # Организация
    WORKS_IN = "WORKS_IN"                         # (Employee) -> (Department)
    HAS_ROLE = "HAS_ROLE"                         # (Employee) -> (Role)
    OWNS = "OWNS"                                 # (Department) -> (Process)
    USES = "USES"                                 # (Department) -> (Location|Equipment|Document)
    RESPONSIBLE_FOR = "RESPONSIBLE_FOR"           # (Role) -> (Process|Step)
    CAN_ACCESS = "CAN_ACCESS"                     # (Role) -> (Document|Location|System)

    # Процессы и документы
    HAS_STEP = "HAS_STEP"                         # (Process) -> (Step)
    NEXT = "NEXT"                                 # (Step) -> (Step)
    NEXT_STEP = "NEXT_STEP"                       # совместимость
    PERFORMED_AT = "PERFORMED_AT"                 # (Step) -> (Location)
    REQUIRES = "REQUIRES"                         # (Step) -> (Document) / (Product) -> (StorageCondition)
    PRODUCES = "PRODUCES"                         # (Step) -> (Document)
    COMPLIES_WITH = "COMPLIES_WITH"               # (Process|Step|Location|System) -> (Regulation)
    DESCRIBES = "DESCRIBES"                       # (Document) -> (Process|Regulation|System)
    SUPERSEDES = "SUPERSEDES"                     # (Document) -> (Document)
    CONTAINS = "CONTAINS"                         # (Document) -> (Chunk)
    CONTAINS_ACTION = "CONTAINS_ACTION"           # (MenuPath) -> (Operation|Step)
    DOCUMENTED_BY = "DOCUMENTED_BY"               # (MenuPath) -> (Document)
    REQUIRES_CONDITION = "REQUIRES_CONDITION"     # (Operation|Step) -> (Condition)
    EXECUTED_BY = "EXECUTED_BY"                   # (Operation|Step) -> (Role)

    # Товары и склад
    OF_PRODUCT = "OF_PRODUCT"                     # (Batch) -> (Product)
    SUPPLIED_BY = "SUPPLIED_BY"                   # (Batch) -> (Supplier)
    STORED_IN = "STORED_IN"                       # (Batch) -> (Location) / (Document) -> (System)
    IN_BATCH = "IN_BATCH"                         # (Pack) -> (Batch)
    PROVIDES = "PROVIDES"                         # (Location) -> (StorageCondition)
    BELONGS_TO = "BELONGS_TO"                     # (Product) -> (Category)
    APPLIES_TO = "APPLIES_TO"                     # совместимость
    INVOLVES = "INVOLVES"                         # (Event) -> (Batch|Product|Equipment)
    HAPPENED_AT = "HAPPENED_AT"                   # (Event) -> (Location)
    PERFORMED_BY = "PERFORMED_BY"                 # (Event) -> (Employee)
    INSTANCE_OF = "INSTANCE_OF"                   # (Event) -> (Process)
    AFFECTS = "AFFECTS"                           # (Excursion) -> (Batch) / (Incident) -> (System|Process|Batch)

    # Информационные системы
    USES_SYSTEM = "USES_SYSTEM"                   # (Department) -> (System)
    OWNS_SYSTEM = "OWNS_SYSTEM"                   # (Department) -> (System)
    ADMINISTERS = "ADMINISTERS"                   # (Employee) -> (System)
    HAS_MODULE = "HAS_MODULE"                     # (System) -> (Module)
    SUPPORTS = "SUPPORTS"                         # (Module) -> (Process)
    EXECUTED_IN = "EXECUTED_IN"                   # (Step) -> (System|Module)
    CREATES = "CREATES"                           # (Step) -> (DataEntity)
    UPDATES = "UPDATES"                           # (Step) -> (DataEntity)
    SOURCE_OF_TRUTH_FOR = "SOURCE_OF_TRUTH_FOR"   # (System) -> (DataEntity)
    STORES = "STORES"                             # (System) -> (DataEntity)
    INTEGRATES_WITH = "INTEGRATES_WITH"           # (System) -> (System)
    FROM = "FROM"                                 # (Integration) -> (System)
    TO = "TO"                                     # (Integration) -> (System)
    TRANSFERS = "TRANSFERS"                       # (Integration) -> (DataEntity)
    GENERATED_BY = "GENERATED_BY"                 # (Document) -> (System)
    REGISTERED_IN = "REGISTERED_IN"               # (Event) -> (System)
    REPORTS_TO = "REPORTS_TO"                     # (Equipment) -> (System)
    HAS_SYSTEM_ROLE = "HAS_SYSTEM_ROLE"           # (Employee) -> (SystemRole)
    IN_SYSTEM = "IN_SYSTEM"                       # (SystemRole) -> (System)
    HAS_MENU = "HAS_MENU"                         # (System) -> (MenuPath)


class SystemType(str, Enum):
    """Категории информационных систем аптечного склада."""
    ERP = "ERP"
    WMS = "WMS"
    TMS = "TMS"
    QMS = "QMS"
    EDO = "EDO"
    GOV_REPORTING = "GovReporting"
    MONITORING = "Monitoring"
    ACS = "ACS"
    BI = "BI"
    OTHER = "Other"


class BatchStatus(str, Enum):
    """Статусы серий лекарственных препаратов и ТМЦ."""
    QUARANTINE = "карантин"
    VALID = "годен"
    DEFECT = "брак"
    WITHDRAWN = "изъят"
    DISPOSAL = "на уничтожение"


# ============================================================================
# 2. Модели сущностей онтологии (Graph Nodes)
# ============================================================================

class BaseGraphNode(BaseModel):
    model_config = ConfigDict(extra="ignore")


# 2.1 Организационные узлы
class OrganizationNode(BaseGraphNode):
    name: str
    inn: str | None = None


class DepartmentNode(BaseGraphNode):
    code: str
    name: str


class EmployeeNode(BaseGraphNode):
    name: str
    access_level: str | None = None


class RoleNode(BaseGraphNode):
    name: str
    title: str | None = None
    code: str | None = None


# 2.2 Процессы и знания
class ProcessNode(BaseGraphNode):
    name: str
    version: str | None = "1.0"
    owner: str | None = None


class StepNode(BaseGraphNode):
    order: int = 1
    description: str
    name: str | None = None


class DocumentNode(BaseGraphNode):
    id: str
    title: str
    type: str | None = "Инструкция"
    number: str | None = None
    version: str | None = "1.0"
    valid_from: str | None = None
    valid_to: str | None = None
    source_url: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RegulationNode(BaseGraphNode):
    code: str
    title: str
    source: str | None = None
    edition: str | None = None


class ConceptNode(BaseGraphNode):
    name: str


class MenuPathNode(BaseGraphNode):
    full_path: str
    section_name: str | None = None

    def get_leaf(self) -> str:
        parts = [p.strip() for p in self.full_path.replace("->", ">").split(">")]
        return parts[-1] if parts else self.full_path


class OperationNode(BaseGraphNode):
    name: str
    verb: str | None = None
    description: str | None = None


class ConditionNode(BaseGraphNode):
    id: str | None = None
    rule_text: str
    severity: str = "NORMAL"


class ChunkNode(BaseGraphNode):
    id: str
    doc_id: str
    chunk_index: int = Field(ge=0)
    text: str
    qdrant_point_id: str | None = None


# 2.3 Товары и склад
class ProductNode(BaseGraphNode):
    id: str
    name: str
    mnn: str | None = None
    form: str | None = None
    dosage: str | None = None


class BatchNode(BaseGraphNode):
    id: str
    number: str
    expiry_date: str | None = None
    status: str = "годен"


class PackNode(BaseGraphNode):
    datamatrix: str
    gtin: str | None = None
    serial: str | None = None


class LocationNode(BaseGraphNode):
    code: str
    type: str = "ячейка"   # склад, зона, стеллаж, ячейка
    temp_min: float | None = None
    temp_max: float | None = None


class StorageConditionNode(BaseGraphNode):
    name: str              # 2–8 °C, 15–25 °C, защита от света, влажность
    temp_min: float | None = None
    temp_max: float | None = None
    light_protected: bool = False
    humidity_max: float | None = None


class CategoryNode(BaseGraphNode):
    name: str              # ЖНВЛП, НС и ПВ, прекурсоры, термолабильные, рецептурные


class SupplierNode(BaseGraphNode):
    name: str
    inn: str | None = None
    license: str | None = None


class CustomerNode(BaseGraphNode):
    name: str
    inn: str | None = None
    license: str | None = None


class EquipmentNode(BaseGraphNode):
    type: str
    name: str | None = None
    calibration_due: str | None = None


class EventNode(BaseGraphNode):
    type: str              # приёмка, отгрузка, списание, инцидент
    timestamp: str | None = None
    status: str | None = None


class ExcursionNode(BaseGraphNode):
    value: float | None = None
    delta: float | None = None
    timestamp: str | None = None


# 2.4 Информационные системы
class SystemNode(BaseGraphNode):
    name: str
    type: str = "ERP"      # ERP, WMS, TMS, QMS, EDO, GovReporting, Monitoring, ACS, BI, Other
    vendor: str | None = None
    version: str | None = None
    criticality: str = "High"


class ModuleNode(BaseGraphNode):
    name: str
    function: str | None = None


class IntegrationNode(BaseGraphNode):
    protocol: str | None = "REST"
    direction: str | None = "двусторонняя"
    frequency: str | None = "realtime"


class DataEntityNode(BaseGraphNode):
    name: str


class SystemRoleNode(BaseGraphNode):
    name: str
    permissions: list[str] = Field(default_factory=list)


class IncidentNode(BaseGraphNode):
    severity: str = "Medium"
    timestamp: str | None = None
    status: str = "Открыт"


# ============================================================================
# 3. Модели связей и доказательной базы (Edges & Provenance)
# ============================================================================

class GraphRelation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_name: str
    source_label: str
    relation_type: str
    target_name: str
    target_label: str
    doc_id: str | None = None
    chunk_id: str | None = None
    weight: int = Field(default=1, ge=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    properties: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 4. Модели для LLM-экстракции (Extraction Payload)
# ============================================================================

class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class ExtractedRelation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    relation: str
    target: str
    context_quote: str | None = None


class ExtractedGraphPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str
    chunk_id: str
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)


# ============================================================================
# 5. Модели для поискового контекста Semantic RAG (Retrieval Models)
# ============================================================================

class GraphFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subject: str
    relation: str
    target: str
    source_doc: str | None = None
    menu_path: str | None = None
    condition: str | None = None
    weight: int = 1

    def to_bullet_point(self) -> str:
        res = f"- {self.subject} [{self.relation}] -> {self.target}"
        if self.menu_path:
            res += f" (Раздел: {self.menu_path})"
        if self.condition:
            res += f" [УСЛОВИЕ: {self.condition}]"
        return res


class GraphRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    anchor_nodes: list[str] = Field(default_factory=list)
    facts: list[GraphFact] = Field(default_factory=list)
    relevant_doc_ids: list[str] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    fallback_used: bool = False
