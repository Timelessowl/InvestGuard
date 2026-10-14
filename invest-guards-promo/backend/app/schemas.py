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


class DataSource(BaseModel):
    id: str
    name: str
    category: str
    schedule: str
    documents: int = Field(ge=0)
    lastRun: str
    status: Literal['active', 'retry']
    share: float = Field(ge=0, le=100)


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


class ExportResult(BaseModel):
    csv: str
    count: int
    exportId: str
