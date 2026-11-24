from typing import Annotated, Any, Literal

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
    # Optional since P1; older cards have none of these.
    kind: Literal['document', 'event', 'manual'] | None = None
    eventId: int | None = None
    eventType: str | None = None
    occurredAt: str | None = None  # ISO date


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
    origin: Literal['demo', 'manual', 'ai', 'events'] = 'demo'
    inn: str | None = None
    risk: 'RiskReport | None' = None


class RiskFlag(BaseModel):
    level: Literal['red', 'yellow', 'green']
    code: str
    title: str
    detail: str = ''


class RiskRating(BaseModel):
    model: str
    rating: float
    level: str


class RiskReport(BaseModel):
    verdict: Literal['red', 'yellow', 'green']
    inn: str
    company: str = ''
    status: str = ''
    flags: list[RiskFlag]
    ratings: list[RiskRating] = Field(default_factory=list)
    checkedAt: str
    source: str = 'Контур.Фокус'


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


SourceKind = Literal['rss', 'dzen', 'trudvsem', 'hh', 'torgi', 'egrz', 'import', 'derived']
SourceGroup = Literal['news', 'hiring', 'land', 'construction', 'infrastructure', 'regional']
Connection = Literal['ready', 'not_configured', 'manual_only', 'no_access', 'error']


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    name: str
    category: str
    url: str = ''
    regional: bool = True
    scheduleMinutes: int = Field(ge=5)
    enabled: bool = True
    kind: SourceKind = 'rss'
    queries: list[str] = Field(default_factory=list)
    group: SourceGroup = 'news'
    dataType: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)  # adapter parameters, never secrets
    access: str | None = None  # what access is needed when it is missing
    docsUrl: str | None = None


class RunStats(BaseModel):
    status: str
    startedAt: str
    finishedAt: str | None = None
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    retries: int = 0
    error: str | None = None


class HiringStats(BaseModel):
    """Job sources and hiring analytics: loaded vacancies and the hiring signals found from them."""
    vacancies: int = 0
    activeVacancies: int = 0
    employers: int = 0
    signals: int = 0
    activeSignals: int = 0


class DataSource(BaseModel):
    id: str
    name: str
    category: str
    url: str = ''
    regional: bool = True
    scheduleMinutes: int = 60
    enabled: bool = True
    # Output stays open to new kinds and groups: the seed is validated by SourceConfig.
    kind: str = 'rss'
    group: str = 'news'
    dataType: str | None = None
    access: str | None = None
    docsUrl: str | None = None
    produces: Literal['documents', 'events'] = 'documents'
    schedule: str
    documents: int = Field(ge=0)
    signals: int = Field(default=0, ge=0)
    events: int = Field(default=0, ge=0)
    eventSignals: int = Field(default=0, ge=0)
    errors7d: int = Field(default=0, ge=0)
    connection: Connection = 'ready'
    lastRunStats: RunStats | None = None
    lastCheck: dict | None = None
    hiring: HiringStats | None = None
    lastRun: str
    lastRunAt: str | None = None
    lastError: str | None = None
    # A source that is not connected is never reported as active.
    status: Literal['active', 'retry', 'not_configured', 'disabled']
    share: float = Field(ge=0, le=100)


class SourceRun(RunStats):
    id: int
    sourceId: str
    trigger: Literal['schedule', 'manual', 'assistant']


class SourceCheck(BaseModel):
    ok: bool
    message: str
    checkedAt: str
    connection: Connection


class CollectResult(BaseModel):
    source: DataSource
    added: int
    queued: bool = False


ProcessingStatus = Literal['new', 'linked', 'waiting', 'review', 'context', 'aggregated', 'ignored', 'error']


class EventTypeConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{1,63}$')]
    title: str
    category: str
    polarity: Literal['positive', 'negative', 'neutral']
    linkPolicy: Literal['company', 'context', 'aggregate']
    stage: str | None = None
    description: str = ''


class EventType(EventTypeConfig):
    model_config = ConfigDict(extra='ignore')
    deprecated: bool = False


class EventLink(BaseModel):
    id: str
    confidence: Literal['confirmed', 'probable', 'manual_review']


class EventOut(BaseModel):
    id: int
    sourceId: str
    externalId: str | None
    type: str
    typeTitle: str
    category: str
    title: str
    companyName: str | None
    inn: str | None
    ogrn: str | None
    municipality: str | None
    address: str | None
    cadastralNumber: str | None
    latitude: float | None
    longitude: float | None
    locationPrecision: str | None
    eventDate: str | None
    status: str | None
    value: float | None
    valueUnit: str | None
    metrics: list[dict]
    url: str | None
    collectedAt: str
    updatedAt: str
    lastSeenAt: str
    processingStatus: ProcessingStatus
    processingReason: str | None
    candidates: list[EventLink]


class EventCandidateLink(EventLink):
    company: str
    linkedBy: Literal['rule', 'analyst']
    reason: str | None
    createdAt: str


class EventVersion(BaseModel):
    replacedAt: str
    contentHash: str
    status: str | None
    fields: dict
    raw: dict


class EventDetail(EventOut):
    sourceCompanyId: str | None
    raw: dict
    duplicateOf: int | None
    links: list[EventCandidateLink]
    versions: list[EventVersion]


class EventPage(BaseModel):
    items: list[EventOut]
    total: int
    limit: int
    offset: int


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


class FocusUsage(BaseModel):
    configured: bool
    used: int
    limit: int


class PipelineStats(BaseModel):
    stages: dict[str, int]
    usage: LlmUsage
    focus: FocusUsage | None = None


class RiskCheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inn: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^(\d{10}|\d{12})$')] | None = None
    force: bool = False


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


Candidate.model_rebuild()
