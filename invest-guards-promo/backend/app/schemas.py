from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Status = Literal['new', 'review', 'approved', 'exported', 'rejected']
Action = Literal['Связаться с компанией', 'Запросить данные', 'Передать эксперту', 'Продолжить мониторинг']
Score = Annotated[float, Field(ge=0, le=100)]


class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    precision: Literal['municipality', 'project']


class Evidence(BaseModel):
    source: str
    date: str
    excerpt: str
    highlights: list[str] = Field(default_factory=list)
    url: str | None = None


class Candidate(BaseModel):
    id: str
    company: str
    initials: str
    sector: str
    city: str
    location: Location | None = None
    signalType: str
    signal: str
    source: str
    sourceDate: str
    score: Score
    urgency: Literal['high', 'medium', 'low']
    urgencyLabel: str
    status: Status
    owner: str
    amount: str
    stage: str
    summary: str
    factors: dict[str, Score]
    recommendation: str
    rationale: str
    nextAction: Action = 'Связаться с компанией'
    evidence: list[Evidence]
    origin: Literal['demo', 'manual', 'ai'] = 'demo'


class NewSignal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)]
    sector: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    signal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)]
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class CandidatePatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['review', 'approved', 'rejected'] | None = None
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)] | None = None
    nextAction: Action | None = None

    @model_validator(mode='after')
    def require_changes(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('Укажите хотя бы одно изменение; пустые значения недопустимы.')
        return self


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    name: str
    category: str
    url: str
    regional: bool
    scheduleMinutes: int = Field(ge=5)
    enabled: bool = True


class DataSource(BaseModel):
    id: str
    name: str
    category: str
    url: str = ''
    regional: bool = True
    scheduleMinutes: int = 60
    enabled: bool = True
    schedule: str
    documents: int = Field(ge=0)
    signals: int = Field(default=0, ge=0)
    lastRun: str
    lastRunAt: str | None = None
    lastError: str | None = None
    status: Literal['active', 'retry']
    share: float = Field(ge=0, le=100)


class DocumentOut(BaseModel):
    id: int
    sourceId: str
    url: str
    title: str
    publishedAt: str | None
    collectedAt: str
    stage: Literal['new', 'skipped', 'rejected', 'signal', 'merged', 'error']
    reason: str | None
    candidateId: str | None


class LlmUsage(BaseModel):
    spentUsd: float
    todayUsd: float
    budgetUsd: float
    dailyBudgetUsd: float
    calls: int
    byPurpose: dict[str, float]
    configured: bool


class PipelineStats(BaseModel):
    stages: dict[str, int]
    usage: LlmUsage


class ChatMessage(BaseModel):
    role: Literal['user', 'assistant']
    content: Annotated[str, StringConstraints(max_length=8000)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class ToolTrace(BaseModel):
    name: str
    arguments: dict
    summary: str


class ChatReply(BaseModel):
    reply: str
    trace: list[ToolTrace]
    changed: bool


class AnalyticsSignal(BaseModel):
    label: str
    value: float


class PilotAnalytics(BaseModel):
    confirmationRate: str
    medianReview: str
    leadTime: str
    signals: list[AnalyticsSignal]
    sourceSignals: int


class State(BaseModel):
    candidates: list[Candidate]
    sources: list[DataSource]
    pilotAnalytics: PilotAnalytics
    pipeline: PipelineStats


class ExportResult(BaseModel):
    csv: str
    count: int
    exportId: str
