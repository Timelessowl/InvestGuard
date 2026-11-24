# InvestGuard — аудит и план разработки

Статус на 24.11.2026. Ветка аудита `feature/agents-rules`: это `develop` (`72e3537`) плюс `AGENTS.md`. Пути в документе указаны от `invest-guards-promo/`.

Текущий объём работ: промпты 1 и 2. Промпт 3 (HeadHunter) отложен: по оценке команды доступ к API hh.ru, скорее всего, получить не удастся. Модуль вакансий всё равно проектируется под два источника, но реализуется только для «Работы России».

---

## 1. Итоги аудита

### 1.1. Источники и сборщики

- Источники описаны в `backend/seed/sources.json` (23 RSS-ленты и Дзен). При каждом старте `database.sync_sources()` записывает их в таблицу `sources` (`id`, `payload JSONB`). Служебные поля `lastRun`, `lastRunAt`, `lastError` и `status` сохраняются, а источники, удалённые из seed, удаляются из таблицы.
- `collectors.collect_source()` — единственная точка сбора. `fetch_items()` выбирает способ по `kind`: для `rss` берёт `httpx.get` и `parse_feed`, для `dzen` вызывает `dzen.search()` через Playwright. Найденные записи вставляются в `documents` с `ON CONFLICT (url) DO NOTHING`. Перехватываются только `httpx.HTTPError` и `CollectError`.
- `collectors.due_sources()` отбирает источники, которым пора на сбор: учитываются `enabled`, `kind` из `SOURCE_KINDS` и время с `lastRunAt` больше `scheduleMinutes`.
- Интерфейса адаптера, повторных попыток, инкрементального курсора, истории запусков и проверки доступности нет. Состояние синхронизации — четыре поля в `sources.payload`.
- `schemas.SourceConfig` объявлен с `extra='forbid'` и `kind: Literal['rss', 'dzen']`. Любое новое поле или тип источника требует правки схемы.

### 1.2. Документы и карточки

- `documents` хранит публикации: `url UNIQUE`, `title`, `body`, `stage`, `extraction`, `embedding JSONB`, `candidate_number`. Таблица заточена под тексты и не подходит для структурированных событий. ТЗ прямо запрещает превращать её в общее хранилище.
- `candidates` хранит карточку целиком в `payload JSONB` (модель `schemas.Candidate`), а `number` — это `INV-NNNN`. Индексов по ИНН, муниципалитету и статусу нет, фильтры выполняются в Python по полному списку.
- `Evidence` состоит из полей `source`, `date`, `excerpt`, `highlights` и `url`. Поле `date` — строка для отображения без года («24 сен, 09:42»). Сортировать по ней хронологию и считать давность нельзя. `sourceDate` карточки устроен так же.
- `Candidate.origin` принимает значения `demo`, `manual` и `ai`. Демо-карточки (6 шт.) загружаются один раз, отметка хранится в `app_metadata.demo_seed_v1`.

### 1.3. Объединение повторных сигналов (`pipeline.process_document`)

1. Префильтр без ИИ: кликбейт, не меньше двух «инвестиционных» корней, упоминание региона для нерегиональных лент.
2. Эмбеддинг сравнивается с документами за 30 дней в стадиях `signal`, `merged` и `rejected`. При косинусе ≥ 0.93 документ отклоняется (повтор отклонённого) или добавляется к карточке соседа.
3. Триаж GigaChat, затем `find_candidate()` ищет карточку по нормализованному названию (`common.normalize_company`) полным перебором карточек в Python. Совпадение — новое доказательство, иначе создаётся новая карточка.
4. `attach_evidence()` отбрасывает доказательство с уже известным URL. Если источник новый (определяется по префиксу строки `source`), «Уверенность» растёт на 6.

ИНН при поиске карточки не используется. Разные проекты одной компании попадают в одну карточку, а одна компания под разными названиями даёт разные карточки.

### 1.4. Определение компании по ИНН

- `pipeline.find_inn()` берёт ИНН из ответа триажа и из текста (регулярное выражение `ИНН\D{0,5}(\d{10}|\d{12})`) и проверяет контрольную сумму (`focus.valid_inn`).
- `pipeline.enrich()` записывает ИНН в карточку, только если там его ещё нет. При `FOCUS_AUTO_CHECK=1` сразу запускается проверка в Фокусе.
- ОГРН нигде не валидируется и не хранится. Таблицы юридических лиц нет.

### 1.5. Контур.Фокус

- `focus.FocusClient`: десять методов API3 по ИНН, кеш в `focus_checks` (TTL 30 дней), лимит уникальных ИНН `FOCUS_INN_LIMIT=950`. Ключ передаётся в query, поэтому логгер `httpx` переведён на WARNING.
- `risk.evaluate()` выставляет красные, жёлтые и зелёные флаги и вердикт. Результат хранится в `payload.risk`. Инвестиционный скоринг он не меняет — разделение, которого требует промпт 8, уже соблюдено.

### 1.6. Текущий скоринг

- Факторы 0–100 («Релевантность», «Уверенность», «Региональный эффект», «Срочность») выставляет GigaChat. Итог считается как `common.overall_score`: 0.35 / 0.25 / 0.2 / 0.2. Срочность переводится в метку функцией `urgency_for`.
- Ручная карточка получает фиксированные 60 баллов, у демо-карточек оценки статичные.
- Весов по типам событий, затухания и учёта независимых источников нет.

### 1.7. Фоновый worker (`worker.py`)

- Однопоточный цикл: `tick()` раз в `PIPELINE_INTERVAL_SECONDS`. Сначала последовательно собираются все «созревшие» источники нужных `SOURCE_KINDS`, затем при настроенном ИИ разбирается пакет документов (`process_pending`, advisory lock `1847302`).
- Запускается отдельным сервисом `worker`, внутри API (`PIPELINE_WORKER=1`) или в сервисе `dzen` (`SOURCE_KINDS=dzen`, `PIPELINE_PROCESS=0`).
- Долгий сбор одного источника задерживает остальные источники и разбор документов в том же такте.

### 1.8. API (`main.py`)

Все маршруты, кроме `/api/health`, закрыты HTTP Basic (middleware и `auth.AuthSettings`). Маршруты: `/api/state`, `/api/candidates` (GET/POST/PATCH), `/api/candidates/{id}/risk-check`, `/api/focus/usage`, `/api/sources`, `/api/sources/{id}/collect`, `/api/documents`, `/api/pipeline/process`, `/api/llm/usage`, `/api/chat`, `/api/analytics` (фиксированные демо-цифры), `/api/exports`. Ошибки переводятся в 404, 409, 422, 429, 502 и 503.

### 1.9. Frontend

- Страницы `QueuePage`, `SourcesPage`, `MapPage` (MapLibre) и `AnalyticsPage`. Компоненты `CandidateDialog`, `RiskPanel`, `ChatPanel`, `AddSignalDialog`.
- `AppProvider` перечитывает `/api/state` каждые 15 секунд. Каждый ответ проверяется схемой Zod в `services/repository.ts`.
- Для совместимости важно: `sourceSchema.kind` — это `z.enum(["rss", "dzen"])`, `status` — `z.enum(["active", "retry"])`, `candidate.origin` — `z.enum(["demo", "manual", "ai"])`. Если бэкенд вернёт новое значение в любом из этих полей, весь `/api/state` не пройдёт валидацию, и интерфейс покажет «Сервер вернул некорректные данные». Незнакомые новые поля Zod просто отбрасывает, это безопасно.

### 1.10. Миграции и тесты

- `database.initialize()` применяет `migrations/*.sql` по имени файла, один раз каждую, в одной транзакции под advisory lock. Файл режется по `;`, и каждый кусок выполняется через `sqlalchemy.text()`. Поэтому в миграциях нельзя ставить `;` внутри строк, функций и `DO`-блоков, а также последовательности вида ` :имя` (их съест разбор bind-параметров).
- Backend: pytest, на каждый тест создаётся и удаляется своя PostgreSQL-база. LLM, RSS и Фокус подменены через `httpx.MockTransport`, Дзен — через monkeypatch. Всего 19 тестов.
- Frontend: Vitest, 4 теста на `selectors.ts`, плюс `tsc --noEmit`. В `package.json` есть скрипт `test:e2e` (Playwright), но в репозитории нет ни конфигурации, ни тестов.
- Результаты прогона на ветке аудита:
  - backend: 12 failed, 7 passed;
  - тот же прогон с внешним плагином, который подставляет Basic-учётные данные в `TestClient`, без изменения кода: 19 passed;
  - frontend: typecheck чистый, vitest 4/4.

---

## 2. Найденные баги и технический долг

Нумерация используется в плане ниже. Где чинить: «Э0» — подготовительный этап, остальное — по номеру промпта.

| # | Проблема | Где | Последствие | Где чинить |
| --- | --- | --- | --- | --- |
| B1 | Бэкенд-тесты не передают учётные данные HTTP Basic, хотя после коммита `1b95464` («nginx auth») middleware закрывает всё, кроме `/api/health`. | `tests/conftest.py`, `tests/test_*.py` (свои `TestClient`) | 12 из 19 тестов падают на 401 (`KeyError` в ответах), регрессии не видны. С подставленными учётными данными проходят все 19. | Э0: фикстура `TestClient(app, auth=(user, AUTH_PASSWORD))` для всех клиентов плюс тест, что без входа отвечает 401 |
| B2 | В README команда тестов не задаёт `AUTH_PASSWORD`, а `lifespan` без него падает с `RuntimeError`. README ссылается на `DEPLOYMENT.md`, которого в репозитории нет. | `README.md` | По инструкции тесты не запускаются, ссылка битая. | Э0 |
| B3 | Триажный ИНН принимается, если сходится контрольная сумма, но не проверяется, что он есть в тексте: `find_inn(triage['inn'], title, body)` первым кандидатом берёт сам ответ модели. | `pipeline.find_inn`, `process_document` | Выдуманный моделью ИНН с верной контрольной суммой (каждый десятый случайный) попадёт в карточку и потратит лимит Фокуса на чужую компанию. | Э0: принимать ИНН модели, только если он встречается в `title` или `body` |
| B4 | `collect_source` перехватывает только `httpx.HTTPError` и `CollectError`. `httpx.InvalidURL` не наследует `HTTPError` (проверено), а ошибки БД (например, NUL-байт в тексте Дзена) тоже пролетают мимо. | `collectors.collect_source`, `worker.tick` | `lastRunAt` не обновляется, исключение обрывает `tick`: оставшиеся источники и разбор документов в этом такте пропускаются, а сломанный источник снова «созрел» на следующем такте. | П1: новый раннер ловит любые ошибки и пишет их в историю запусков |
| B5 | `find_candidate` сравнивает только нормализованное название и перебирает все карточки в Python. `normalize_company` удаляет слова «завод», «компания», «предприятие», «холдинг». | `pipeline.find_candidate`, `common.normalize_company` | Ложные склейки («Уральский завод» и «Уральский») и пропуски (одна компания под разными названиями), разные проекты одной компании попадают в одну карточку, запрос растёт линейно. | П7 |
| B6 | `enrich` молча игнорирует новый ИНН, если в карточке уже записан другой. | `pipeline.enrich` | Расхождение ИНН — главный признак ошибочной склейки — нигде не фиксируется. | П7: такой случай отправлять на ручную проверку |
| B7 | Даты доказательств и `sourceDate` хранятся строками без года. | `pipeline.evidence_for`, `Evidence.date` | Нельзя построить хронологию и затухание. | П1: необязательное ISO-поле `occurredAt` |
| B8 | Жёсткие enum в Zod (`kind`, `status`, `origin`). | `frontend/src/domain/models.ts` | Новое значение с бэкенда ломает весь интерфейс. | П1, одновременно с бэкендом |
| B9 | `npm run check` вызывает `test:e2e`, но тестов Playwright нет. | `frontend/package.json` | Проверка `check` не проходит. | П9/П10: добавить конфигурацию и smoke-тесты |
| B10 | При одобрении без ответственного подставляется демо-имя «А. Крылова». | `service.patch_candidate` | Демо-логика в рабочих данных. | П9 (согласовать с командой) |
| B11 | `/api/health` всегда отвечает `mode: 'demo'`, `/api/analytics` отдаёт фиксированные демо-цифры. | `main.py` | Аналитика выдаётся за реальную. | П9 |
| B12 | У nginx не задан `client_max_body_size` (по умолчанию 1 МБ). | `frontend/nginx.conf` | Загрузка CSV больше 1 МБ получит 413. | П6 |
| B13 | Разбор миграций по `;` (см. 1.10). | `database.initialize` | Ограничивает SQL новых миграций. | П1: держать DDL простым либо выполнять файл целиком через `exec_driver_sql` |

---

## 3. Последовательность задач и зависимости

```
Э0 (тесты, B1–B3)
 └─► П1 source_events + адаптеры + раннер + связь с карточками
      ├─► П2 Работа России + модуль вакансий ──► (П3 HeadHunter — отложен, нужен доступ)
      ├─► П6 импорт CSV ─────────────┬─► П4 ГИС Торги (API или резерв через импорт)
      │                              └─► П5 ЕГРЗ / ГИСОГД (API или резерв через импорт)
      └─► П7 организации и проекты ──► П8 скоринг, намерения и препятствия ──► П9 интерфейс ──► П10
```

| Задача | Ветка | Зависит от | Можно параллельно с |
| --- | --- | --- | --- |
| Э0 | `feature/test-auth` | — | — |
| П1 | `feature/source-events` | Э0 | — |
| П2 | `feature/trudvsem` (от П1) | П1 | П6 |
| П3 | `feature/hh` | П2, ключи hh.ru | — (отложен) |
| П6 | `feature/data-import` | П1 | П2 |
| П4 | `feature/torgi` | П1, П6 (резерв) | П5 |
| П5 | `feature/egrz` | П1, П6 (резерв) | П4 |
| П7 | `feature/projects` | П1; данные П2/П6 полезны для тестов | П4, П5 |
| П8 | `feature/lead-scoring` | П7 | — |
| П9 | `feature/ui-events` | API П1–П8 (минимальный UI идёт вместе с П1, П2, П6) | — |
| П10 | `feature/release-check` | всё | — |

Правила:
- одна задача — одна ветка;
- пока предыдущая ветка не слита, следующая строится стеком поверх неё (П2 от П1); PR и слияние — по решению команды;
- номер миграции назначается в момент слияния, применённые миграции не переименовываются;
- бэкенд и frontend-схемы Zod меняются в одном коммите, если меняются enum.

Порядок реализации: Э0 → П1 → П2 → П6 → П7 → П4 → П5 → П8 → П9 → П10. П4 и П5 после П6, потому что без подтверждённых API они сводятся к шаблонам импорта.

---

## 4. Этап 0. Подготовка (до промпта 1)

- **Изменить:**
  - `backend/tests/conftest.py` — фикстура `client` и фабрика `api_client(app)` с `auth=('poc_user', os.environ['AUTH_PASSWORD'])`;
  - `tests/test_api.py`, `test_pipeline.py`, `test_sources.py`, `test_focus.py` — все `TestClient(...)` через фабрику;
  - `pipeline.find_inn` (B3);
  - `README.md` (B2): команда тестов как в `AGENTS.md`, убрать или заменить ссылку на `DEPLOYMENT.md`.
- **Тесты:**
  - 401 без учётных данных и с неверным паролем, `/api/health` открыт;
  - ИНН из ответа модели, которого нет в тексте, не сохраняется.
- **Внешние зависимости:** нет.

---

## 5. Промпт 1. Универсальная архитектура источников и событий

### 5.1. Что используем
`collectors.parse_feed` и `fetch_items` (без изменений логики), `dzen.search`, `database` (`SourceRecord`, `initialize`, `sync_sources`), `focus.valid_inn`, `geo.canonical_municipality` и `geo.lookup`, `pipeline.attach_evidence`, `common.display_date` и `normalize_company`, `net.http_client`, advisory locks по образцу `pipeline.process_pending`.

### 5.2. Файлы

Создать:

| Файл | Назначение |
| --- | --- |
| `backend/app/sources/__init__.py` | Реестр адаптеров: `register(kind)`, `adapter_for(source_payload, http)` |
| `backend/app/sources/base.py` | `SourceAdapter`, `EventItem`, `Batch`, `Availability`, иерархия ошибок, `request_json()` с повторами, `keep_fields()` (белый список полей для `raw`) |
| `backend/app/sources/news.py` | `RssAdapter`, `DzenAdapter`: обёртки над `collectors.fetch_items` |
| `backend/app/sources/runner.py` | `sync_source()`, `due_sources()`: блокировка, батчи, курсор, статистика, бэкофф |
| `backend/app/events.py` | Реестр типов событий, `dedup_key()`, `content_hash()`, `upsert_events()`, выборки для API |
| `backend/app/linking.py` | `link_pending_events()`: правила связи событий с карточками |
| `backend/seed/event_types.json` | Справочник типов событий |
| `backend/seed/linking_rules.json` | Пороги создания карточки и окна |
| `backend/migrations/004_source_events.sql` | Таблицы (см. 5.4) |
| `backend/tests/test_events.py`, `test_source_runner.py`, `test_linking.py` | Тесты |
| `frontend/src/pages/EventsPage.tsx` | Минимальный список событий с фильтрами (полная версия — П9) |

Изменить:
- `collectors.py`:
  - `CollectError` наследует `SourceError`;
  - `collect_source()` становится совместимой обёрткой над `runner.sync_source()` и по-прежнему возвращает `(payload, added)`, её используют `main.py`, `agent.py` и тесты;
  - `due_sources` реэкспортируется.
- `worker.py`: `tick()` = сбор созревших источников → `process_pending` (если настроен ИИ) → `link_pending_events` (без ИИ, всегда); отдельный advisory lock `1847303`.
- `database.py`: `sync_event_types()` в `initialize()`; типы событий не удаляются, отсутствующие в seed помечаются `deprecated`.
- `schemas.py`:
  - `SourceConfig` и `DataSource`: расширенный `kind`, новые поля (см. 5.6);
  - `Evidence`: новые необязательные поля;
  - `Candidate.origin` += `events`;
  - новые `EventOut`, `EventDetail`, `EventPage`, `SourceRun`, `SourceCheck`.
- `service.py`: `list_sources()` добавляет счётчики событий, статус подключения и последний запуск; функции `list_events`, `get_event`, `candidate_events`.
- `main.py`: новые маршруты (см. 5.7), `collect` для источников событий ставит запуск в очередь.
- `pipeline.evidence_for()`: дописывает `kind='document'` и `occurredAt` (ISO). Поведение разбора не меняется.
- `agent.collect_sources()`: без `source_id` собирает только новостные источники, чтобы чат случайно не запустил полную выгрузку API.
- `.env.example`, `compose.yaml` (переменные из 5.9), `README.md` (раздел «Источники событий»).
- Frontend: `domain/models.ts`, `services/repository.ts`, `pages/SourcesPage.tsx`, `app/App.tsx`, `app/Shell.tsx` (пункт меню «События»).

### 5.3. Интерфейс адаптера

```python
# backend/app/sources/base.py
class SourceError(Exception):
    retryable = True                      # сетевые сбои, 5xx
class SourceRateLimited(SourceError):     # 429; retry_after из заголовка Retry-After
    def __init__(self, message, retry_after=None): ...
class SourceAccessDenied(SourceError):    # 401/403: ключ не действует, повтор бесполезен
    retryable = False
class SourceNotConfigured(SourceError):   # нет ключа или доступа
    retryable = False
class SourceDataError(SourceError):       # ответ не соответствует ожидаемой схеме
    retryable = False

Availability = Literal['ready', 'not_configured', 'manual_only', 'no_access']

@dataclass(frozen=True)
class EventItem:
    event_type: str                       # код из event_types
    title: str
    external_id: str | None = None
    company_name: str | None = None
    inn: str | None = None                # только с верной контрольной суммой, иначе None
    ogrn: str | None = None
    source_company_id: str | None = None  # ID работодателя или организации в источнике
    municipality: str | None = None       # каноническое имя geo.py или None
    address: str | None = None
    cadastral_number: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_precision: str | None = None # exact | address | municipality
    event_date: date | None = None
    status: str | None = None
    value: Decimal | None = None          # основной показатель
    value_unit: str | None = None
    metrics: tuple[dict, ...] = ()        # [{'name': 'work_places', 'value': 3, 'unit': 'чел.'}]
    url: str | None = None
    raw: dict = field(default_factory=dict)  # только белый список полей, без персональных данных
    details_kind: str | None = None       # 'vacancy', 'land_lot', ... — запись расширения
    details: dict | None = None

@dataclass
class Batch:
    documents: list[dict] = field(default_factory=list)   # формат parse_feed
    events: list[EventItem] = field(default_factory=list)
    cursor: dict | None = None            # сохраняется после записи батча
    snapshot_complete: bool = False       # последний батч полной выгрузки: можно закрывать пропавшие записи

class SourceAdapter(ABC):
    kind: ClassVar[str]
    produces: ClassVar[Literal['documents', 'events']]
    scheduled: ClassVar[bool] = True      # импорт: False
    required_env: ClassVar[tuple[str, ...]] = ()

    def __init__(self, source: dict, http: httpx.Client): ...
    def availability(self) -> Availability: ...           # без сети, по env и конфигурации
    def check(self) -> tuple[bool, str]: ...              # дешёвый запрос (limit=1), в БД не пишет
    @abstractmethod
    def fetch(self, cursor: dict | None) -> Iterator[Batch]: ...
    def request_json(self, url, params=None, *, attempts=3) -> dict: ...
    # повторы: TransportError, 5xx, 429 (Retry-After, не больше SOURCE_MAX_RETRY_AFTER);
    # 401/403 → SourceAccessDenied; некорректный JSON → SourceDataError
```

Реестр: `@register('rss')`, `@register('dzen')`, `@register('trudvsem')` и т. д. `adapter_for(payload, http)` по `payload['kind']` возвращает экземпляр. Неизвестный `kind` даёт статус `error` («адаптер не найден»), без падения.

### 5.4. PostgreSQL: `004_source_events.sql`

Учитываем ограничения раннера: без `;` внутри строк и без ` :имя`. Регулярные выражения записаны через `[0-9]`, а не `\d`.

```sql
CREATE TABLE event_types (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,           -- hiring | site | infrastructure | construction | equipment | corporate | other
    polarity TEXT NOT NULL CHECK (polarity IN ('positive', 'negative', 'neutral')),
    link_policy TEXT NOT NULL CHECK (link_policy IN ('company', 'context', 'aggregate')),
    deprecated BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE source_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id TEXT NOT NULL,          -- без FK: sync_sources удаляет источники, события остаются
    external_id TEXT,
    dedup_key TEXT NOT NULL,          -- 'id:' || external_id, иначе 'h:' || sha256 значимых полей
    event_type TEXT NOT NULL REFERENCES event_types(code),
    title TEXT NOT NULL,
    company_name TEXT,
    inn TEXT CHECK (inn ~ '^([0-9]{10}|[0-9]{12})$'),
    ogrn TEXT CHECK (ogrn ~ '^([0-9]{13}|[0-9]{15})$'),
    source_company_id TEXT,
    municipality TEXT,
    address TEXT,
    cadastral_number TEXT,
    latitude DOUBLE PRECISION CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION CHECK (longitude BETWEEN -180 AND 180),
    location_precision TEXT CHECK (location_precision IN ('exact', 'address', 'municipality')),
    event_date DATE,
    status TEXT,
    value NUMERIC,
    value_unit TEXT,
    metrics JSONB NOT NULL DEFAULT '[]'::jsonb,
    url TEXT,
    raw JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- первое получение
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),     -- последнее изменение содержимого
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- последний раз видели в источнике
    processing_status TEXT NOT NULL DEFAULT 'new' CHECK (processing_status IN
        ('new', 'linked', 'waiting', 'review', 'context', 'aggregated', 'ignored', 'error')),
    processing_reason TEXT,
    processed_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    duplicate_of BIGINT REFERENCES source_events(id) ON DELETE SET NULL,
    UNIQUE (source_id, dedup_key)
);
CREATE INDEX source_events_type_idx ON source_events (event_type, event_date DESC);
CREATE INDEX source_events_inn_idx ON source_events (inn) WHERE inn IS NOT NULL;
CREATE INDEX source_events_municipality_idx ON source_events (municipality);
CREATE INDEX source_events_processing_idx ON source_events (processing_status, collected_at);
CREATE INDEX source_events_source_idx ON source_events (source_id, collected_at DESC);
CREATE INDEX source_events_company_idx ON source_events (source_id, source_company_id);
CREATE TABLE source_event_versions (
    event_id BIGINT NOT NULL REFERENCES source_events(id) ON DELETE CASCADE,
    replaced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash TEXT NOT NULL,
    status TEXT,
    raw JSONB NOT NULL,
    PRIMARY KEY (event_id, replaced_at)
);
CREATE TABLE candidate_events (
    candidate_number BIGINT NOT NULL REFERENCES candidates(number) ON DELETE CASCADE,
    event_id BIGINT NOT NULL REFERENCES source_events(id) ON DELETE CASCADE,
    confidence TEXT NOT NULL CHECK (confidence IN ('confirmed', 'probable', 'manual_review')),
    linked_by TEXT NOT NULL CHECK (linked_by IN ('rule', 'analyst')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (candidate_number, event_id)
);
CREATE INDEX candidate_events_event_idx ON candidate_events (event_id);
CREATE TABLE source_sync_state (
    source_id TEXT PRIMARY KEY,
    sync_cursor JSONB,
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    requested_at TIMESTAMPTZ,         -- ручной запуск из API: подхватывает worker
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_check_at TIMESTAMPTZ,
    last_check_ok BOOLEAN,
    last_check_message TEXT
);
CREATE TABLE source_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id TEXT NOT NULL,
    run_trigger TEXT NOT NULL CHECK (run_trigger IN ('schedule', 'manual', 'assistant')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN
        ('running', 'ok', 'partial', 'error', 'not_configured', 'skipped')),
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX source_runs_source_idx ON source_runs (source_id, started_at DESC);
ALTER TABLE candidates ADD COLUMN inn_key TEXT GENERATED ALWAYS AS (payload ->> 'inn') STORED;
CREATE INDEX candidates_inn_idx ON candidates (inn_key) WHERE inn_key IS NOT NULL;
```

Почему так:
- **Связь с карточкой.** Отдельная таблица `candidate_events` (многие-ко-многим) вместо колонки `candidate_number`. Ручное объединение и разделение карточек в П7 сводится к переносу строк, а одно событие при необходимости подтверждает два проекта. Поле ТЗ «Связь с инвестиционной карточкой» отдаётся в API как `candidates: [{id, confidence}]`.
- **Генерируемая колонка `inn_key`.** Даёт индекс по ИНН без изменения путей записи карточек: ORM колонку не пишет, `payload` остаётся источником истины. Старые карточки получают `inn_key = NULL` или свой ИНН.
- **`documents` не трогаем.** RSS и Дзен пишут туда же, как раньше.

### 5.5. Дедупликация, инкремент, повторы

- **Ключ дедупликации.** `dedup_key`: при наличии `external_id` — `id:<external_id>`, иначе `h:<sha256>` от канонического JSON полей `event_type`, ИНН/ОГРН (или нормализованное название), `event_date`, `cadastral_number`, нормализованный адрес, нормализованный заголовок, `value`. `content_hash` — sha256 всех значимых полей, кроме служебных дат.
- **`upsert_events(db, source_id, items)`.**
  1. Читает существующие строки по `(source_id, dedup_key = ANY(:keys))` с `FOR UPDATE`.
  2. Новые вставляет. У неизменившихся обновляет только `last_seen_at`.
  3. Изменившиеся сначала копирует в `source_event_versions`, затем обновляет поля, `updated_at` и `content_hash`. Если `processing_status` не `ignored`, сбрасывает его в `new`: связь пересчитается, а существующее доказательство обновится по `eventId`.
  4. Возвращает `(inserted, updated, unchanged)`.
  5. Повторы внутри одного батча схлопываются до записи.
- **Расширения.** Если у элемента есть `details_kind`, после вставки вызывается зарегистрированный писатель расширения (`vacancy` → `vacancies.store.save_details`).
- **Раннер.** `runner.sync_source(sessions, source_id, http, trigger)`:
  1. `pg_try_advisory_lock(1847310, hashtext(source_id))` на отдельном соединении. Если занято, запуск получает `skipped` («уже выполняется»).
  2. Создаётся строка `source_runs`. Если `availability() != 'ready'`, запуск завершается статусом `not_configured` без сетевых запросов.
  3. Для каждого `Batch` записываются документы (`INSERT … ON CONFLICT (url) DO NOTHING`, как сейчас) и события. Курсор сохраняется, если он есть, затем `COMMIT`. Данные остаются при сбое на следующей странице, а повтор безопасен благодаря upsert.
  4. Ошибки: `SourceError` → `error` с сообщением; любое другое исключение → `log.exception` и `error` с текстом «Внутренняя ошибка: <тип>». Исключение наружу не выходит (B4).
  5. Бэкофф: `next_run_at = now + min(schedule, SOURCE_BACKOFF_BASE_MINUTES · 2^(failures-1))`.
  6. В конце, для совместимости, обновляются `sources.payload.lastRun`, `lastRunAt`, `lastError` и `status`.
  7. Зависшие `running` старше `SOURCE_RUN_STALE_MINUTES` при старте worker помечаются как `error`.
- **Курсор.** Адаптер сам определяет формат. Типовой вариант — `{"modified_from": <начало последнего успешного запуска − перекрытие>}`. Курсор двигается только после полностью успешного запуска.
- **Созревший источник.** `due_sources`: `enabled`, `kind` из `SOURCE_KINDS`, `adapter.scheduled`, и либо `requested_at > last_attempt_at`, либо наступил `next_run_at` (без записи состояния — по `lastRunAt` и `scheduleMinutes`, как сейчас).

### 5.6. Конфигурация источника

В `SourceConfig` и `DataSource` добавляются поля:
- `kind`: `rss | dzen | trudvsem | hh | torgi | egrz | import | derived`;
- `group` — для группировки на странице: `news | hiring | land | construction | infrastructure | regional`;
- `dataType` — подпись типа данных;
- `options: dict` — параметры адаптера, не секреты;
- `access: str | None` — какой доступ нужен, если его нет;
- `docsUrl`.

Секреты берутся только из env. В выдаче `DataSource` появляются:
- `connection`: `ready | not_configured | manual_only | no_access | error`;
- `events`;
- `eventSignals`;
- `errors7d`;
- `lastRunStats`.

`status` расширяется до `active | retry | not_configured | disabled`. Неподключённый источник никогда не получает `active`.

### 5.7. API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/events` | Список: `type` (можно несколько), `inn`, `company` (ILIKE с экранированием, как в `list_documents`), `municipality` (канонизируется), `source`, `processing`, `candidate`, `date_from`, `date_to`, `limit` ≤ 200, `offset`. Ответ `{items, total, limit, offset}` |
| GET | `/api/events/{id}` | Полная карточка: поля, `raw`, `metrics`, связи с карточками, версии, `duplicateOf` |
| GET | `/api/candidates/{id}/events` | События, связанные с карточкой, по `event_date` |
| GET | `/api/event-types` | Справочник типов |
| POST | `/api/sources/{id}/collect` | Совместимо. RSS и Дзен собираются синхронно, как сейчас. Для источников событий выставляется `requested_at`, ответ `{source, added: 0, queued: true}`: полная выгрузка идёт минутами и не уложится в таймаут nginx (130 с) и frontend (30 с) |
| POST | `/api/sources/{id}/check` | Проверка доступности (`adapter.check()`, таймаут 10 с), результат в `source_sync_state` |
| GET | `/api/sources/{id}/runs` | Последние запуски со статистикой и ошибками |

Все новые маршруты закрыты той же HTTP Basic.

### 5.8. Связь событий с карточками (`linking.py`, без ИИ)

`link_pending_events(sessions, limit)` разбирает события в `new` по правилам, первое сработавшее правило побеждает:

1. Политика типа `aggregate` (одиночная вакансия) → `aggregated`. Учитывается только через кадровые сигналы (П2).
2. Политика `context` (лот без победителя, публикация торгов) → `context`. Компания к лоту не привязывается (требование П4).
3. Нет валидного ИНН и ОГРН → `review` с причиной «нет идентификатора юрлица». Сопоставление по названию только предлагается, автоматически не связывается.
4. ИНН есть, в `candidates.inn_key` найдено 0 карточек. Проверяется порог `linking_rules.json`: в окне `windowDays` (180) по этому ИНН есть положительные события не меньше `minCategoriesForCard` (2) разных категорий, все в одном муниципалитете (или без муниципалитета).
   - Порог выполнен → создаётся карточка `origin='events'`: `stage` по типам событий, `sector` «Не определена», `amount` «Не указано», детерминированное резюме из фактов, доказательства из событий, `status='new'`. Все события получают `linked/confirmed`.
   - Не выполнен → `waiting`.
   - Муниципалитеты расходятся → `review`.
5. Ровно одна карточка с этим ИНН.
   - Муниципалитет совпадает или у события его нет → `linked/confirmed`, `attach_evidence` с `eventId`.
   - Другой муниципалитет → `review` («возможно, другой проект компании»).
6. Несколько карточек с этим ИНН. Ровно одна совпадает по кадастровому номеру, адресу или муниципалитету → `linked/probable`, иначе `review`.

Доказательство из события выглядит так:

```python
{'source': '<источник> · <тип>', 'date': display_date(...), 'excerpt': '<заголовок + ключевые показатели>',
 'url': ..., 'kind': 'event', 'eventId': ..., 'eventType': ..., 'occurredAt': 'YYYY-MM-DD'}
```

`attach_evidence` считает дублем то же значение `eventId` или тот же URL. Бонус «Уверенности» за новый источник для событий не начисляется: в П8 его заменит скоринг.

Автопроверка Фокуса для карточек из событий выключена по умолчанию (`FOCUS_AUTO_CHECK_EVENTS=0`). Тысячи ИНН работодателей иначе за день исчерпают лимит в 950 компаний.

### 5.9. Встраивание в worker и настройки

- `tick()`: `due_sources` → `sync_source` для каждого (ошибки внутри, B4) → `process_pending` (ИИ) → `link_pending_events`.
- В `compose.yaml` отдельный сервис `collector` (тот же образ, `SOURCE_KINDS=trudvsem`, `PIPELINE_PROCESS=0`), чтобы долгая выгрузка API не задерживала RSS и разбор. Docker в рамках задачи не запускаем, поэтому изменение compose локально не проверяется. Это отмечается в отчёте.
- Новые переменные в `.env.example`: `SOURCE_RETRY_ATTEMPTS=3`, `SOURCE_BACKOFF_BASE_MINUTES=5`, `SOURCE_MAX_RETRY_AFTER=300`, `SOURCE_RUN_STALE_MINUTES=60`, `EVENT_LINK_BATCH=200`, `FOCUS_AUTO_CHECK_EVENTS=0`.

### 5.10. Frontend (минимум для совместимости)

- `models.ts`:
  - `sourceSchema.kind` → `z.string()`;
  - `status` расширить;
  - новые поля сделать необязательными (`.optional()` или `.default(...)`);
  - `origin` += `events`;
  - у `evidence` необязательные `kind`, `eventId`, `eventType`, `occurredAt`;
  - схемы `eventSchema` и `eventPageSchema`.
- `repository.ts`: `events(filters)`, `event(id)`, `candidateEvents(id)`, `checkSource(id)`. `collectSource` понимает `queued`.
- `SourcesPage`:
  - подпись статуса по словарю («Требуется настройка», «Нет доступа», «Отключён»);
  - для источников событий выводить «N событий»;
  - уведомление «Сбор поставлен в очередь».
- `EventsPage`: таблица с фильтрами «тип», «ИНН», «муниципалитет», «источник», «статус обработки», ссылка на первоисточник, раскрытие `raw`.

### 5.11. Тесты П1

- Создание события фейковым адаптером (зарегистрирован в тесте): поля, `metrics`, исходный JSON в `raw`.
- Повторная загрузка: `inserted=0`, `unchanged=N`. Изменённое содержимое даёт `updated=1` и строку в `source_event_versions`. Одно событие дважды в одном батче сохраняется один раз.
- Ошибки источника через MockTransport:
  - 500 → повторы и затем `retry`/`error`, `lastError`, запись в `source_runs`;
  - 429 с `Retry-After`;
  - 403 → `SourceAccessDenied` без повторов;
  - битый JSON → `SourceDataError`;
  - неожиданное исключение не обрывает `tick`: остальные источники собираются (регрессия B4).
- Не настроенный адаптер: ни одного HTTP-запроса, статус `not_configured`, источник не показывается как работающий.
- Связь с кандидатом:
  - по ИНН с тем же муниципалитетом → `confirmed`;
  - другой муниципалитет → `review`;
  - без ИНН → `review`;
  - порог из двух категорий создаёт карточку `origin='events'`;
  - контекстное событие не связывается.
- Сохранность старых карточек: демо-карточки и карточки из RSS после миграции проходят валидацию и отдаются API без изменений, кроме новых полей со значениями по умолчанию; `inn_key` заполнен.
- API: фильтры, пагинация, 404, валидация `inn`, закрытость без входа.
- Существующие тесты RSS и Дзена проходят без правок (кроме Э0).
- Frontend (Vitest): старый и новый `/api/state` проходят схему, неизвестный `kind` не ломает разбор.

### 5.12. Внешние зависимости и доступы
Новых пакетов нет: хватает `httpx`, `pydantic` и `SQLAlchemy`. Внешних доступов не требуется.

---

## 6. Промпт 2. «Работа России» и общий модуль вакансий

### 6.1. Что проверено (пробные запросы 24.11.2026)

- Рабочий API — `https://opendata.trudvsem.ru/api/v1` (HTTPS и HTTP оба отвечают), ключ не нужен. Документация, указанная в ТЗ, лежит на dev-стенде `web-mbl-dev01.k8s.trudvsem.ru/opendata/api` и отдаётся как HTML-приложение. Перед реализацией сверить параметры с ней вручную; HTML не разбирать.
- `GET /vacancies/region/6600000000000` — вакансии Свердловской области. В ответе `region.region_code = 6600000000000`, `name = «Свердловская область»`. На дату проверки `meta.total` = 16 431.
- **Пагинация.** `offset` — номер страницы, а не смещение: `offset=1&limit=3` вернул записи 4–6. `limit` ограничен 100 (при запросе 101 ответ содержит `meta.limit=100`). Полная выгрузка — около 165 запросов.
- **Инкремент.** `modifiedFrom=2026-11-23T00:00:00Z` вернул 2 120 записей, около 22 запросов в сутки.
- Есть выборка по компании: `/vacancies/company/inn/{inn}` и `/vacancies/company/{ogrn}`.
- **Поля вакансии** (выборка из 100):
  - `id`, `source` («Вакансия работодателя» — 98, «Вакансия интернет ресурса» — 2), `creation-date`, `date_modify`;
  - `salary_min`, `salary_max`, `currency`, `job-name`, `vac_url`, `schedule`, `employment` (у 47%);
  - `category.specialisation`, `code_profession` (95%), `work_places` (у 75% — 1), `duty`, `requirements`;
  - `addresses.address[].location`, `lat`, `lng`;
  - `company{inn (100%), ogrn, kpp, companycode, name, hr-agency, url, email, site}`.
- **Персональные данные.** `contact_person`, `contact_list` (телефоны и почта), `company.email` (бывает именной адрес сотрудника). Их не сохраняем: `raw` собирается по белому списку полей.
- **Состав выборки.** Велика доля бюджетной сферы: «Образование, наука» — 22%, «ЖКХ» — 14%, «Здравоохранение» — 8%. «Производство» и «Металлургия» вместе — 15%. Без исключения бюджетных организаций кадровые сигналы будут ложными.
- Лимиты частоты в ответах не видны. Работаем вежливо: пауза между страницами, обработка 429 и 503.

### 6.2. Состав модуля

Создать:

| Файл | Назначение |
| --- | --- |
| `backend/app/vacancies/__init__.py` | Общий модуль для П2 и П3 |
| `backend/app/vacancies/model.py` | `Vacancy` (pydantic): нормализованная вакансия; `employer_key()`, `title_norm()`, `fingerprint()`, `municipality_from_address()` |
| `backend/app/vacancies/categories.py` | Загрузка и валидация справочника, `classify(title, specialisation, profession_code) -> (category, profile)`, `is_excluded_employer()` |
| `backend/app/vacancies/store.py` | Писатель расширения `vacancy`: upsert `vacancy_details` и `vacancy_employers`, закрытие пропавших вакансий после полной сверки |
| `backend/app/vacancies/aggregates.py` | Показатели по работодателю, дневные снимки |
| `backend/app/vacancies/signals.py` | Правила кадровых сигналов → `source_events` источника `hiring-signals` |
| `backend/app/sources/trudvsem.py` | `TrudvsemAdapter`: API → `Vacancy` → `EventItem(vacancy_opened, details_kind='vacancy')` |
| `backend/seed/vacancy_categories.json` | Справочник категорий профессий и исключений |
| `backend/seed/hiring_signals.json` | Пороги сигналов |
| `backend/migrations/005_vacancies.sql` | Таблицы ниже |
| `backend/tests/test_trudvsem.py`, `test_vacancies.py`, `tests/fixtures/trudvsem_page_*.json` | Тесты; фикстуры обезличены, без контактов |

Изменить:
- `seed/sources.json`: `{"id": "trudvsem", "name": "Работа России", "category": "Кадровые источники", "group": "hiring", "kind": "trudvsem", "url": "https://trudvsem.ru/", "regional": true, "scheduleMinutes": 360, "options": {"regionCode": "6600000000000", "pageSize": 100}}` и виртуальный `{"id": "hiring-signals", "name": "Кадровая аналитика", "kind": "derived", ...}` (не по расписанию).
- `seed/event_types.json`: `vacancy_opened` (aggregate), `vacancy_growth`, `vacancy_mass_hiring`, `vacancy_new_municipality`, `vacancy_production_launch` (все company/positive/hiring).
- `worker.py`: после сбора `trudvsem` пересчитать показатели по затронутым работодателям, раз в сутки — снимок по всем. Отметка последнего снимка хранится в `app_metadata`.
- `.env.example`, `compose.yaml` (сервис `collector`), `README.md`.
- Frontend `SourcesPage`: для `trudvsem` выводить загруженные вакансии, обнаруженные кадровые сигналы, последнюю синхронизацию и ошибки. В `EventsPage` — пресет «Кадровые сигналы».

### 6.3. Нормализация (`Vacancy`)

| Поле | Источник в «Работе России» | Правило |
| --- | --- | --- |
| `external_id` | `id` | — |
| `employer_name`, `inn`, `ogrn`, `kpp` | `company.*` | ИНН и ОГРН — только с верной контрольной суммой, иначе `None` (не выдумываем) |
| `source_employer_id` | `company.companycode` | — |
| `hr_agency` | `company.hr-agency` | — |
| `title` | `job-name` | `title_norm`: нижний регистр, ё → е, без скобок и пола («(м/ж)») |
| `description` | `duty` + `requirements` | обрезка до 3000 символов |
| `municipality` | `addresses.address[0].location` | `geo.canonical_municipality` по части после «Свердловская область, г »; иначе `None` |
| `address`, `latitude`, `longitude` | `addresses.address[0]` | `location_precision='address'`, если есть координаты |
| `published_at` | `creation-date` | — |
| `modified_at` | `date_modify` | — |
| `work_places` | `work_places` | `None`, если нет |
| `salary_min`, `salary_max`, `currency` | одноимённые | — |
| `employment`, `schedule` | одноимённые | — |
| `specialisation`, `profession_code` | `category.specialisation`, `code_profession` | — |
| `url` | `vac_url` | — |
| `syndicated` | `source == 'Вакансия интернет ресурса'` | Перепубликация с другого сайта, кандидат в межисточниковые дубли |

`employer_key`:
- `inn:<ИНН>` при валидном ИНН;
- иначе `ogrn:<ОГРН>`;
- иначе `<source_id>:<source_employer_id>`.

Названия в ключе не используются никогда.

### 6.4. PostgreSQL: `005_vacancies.sql`

```sql
CREATE TABLE vacancy_employers (
    employer_key TEXT PRIMARY KEY,
    inn TEXT,
    ogrn TEXT,
    name TEXT NOT NULL,
    source_ids TEXT[] NOT NULL DEFAULT '{}',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),   -- начало наблюдения в InvestGuard
    excluded_reason TEXT                                -- кадровое агентство, бюджетная организация
);
CREATE TABLE vacancy_details (
    event_id BIGINT PRIMARY KEY REFERENCES source_events(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    employer_key TEXT NOT NULL REFERENCES vacancy_employers(employer_key),
    title_norm TEXT NOT NULL,
    category TEXT,
    profile TEXT NOT NULL DEFAULT 'other' CHECK (profile IN
        ('production', 'engineering', 'production_management', 'executive', 'other')),
    profession_code TEXT,
    specialisation TEXT,
    municipality TEXT,
    work_places INTEGER CHECK (work_places > 0),
    salary_min NUMERIC,
    salary_max NUMERIC,
    currency TEXT,
    employment TEXT,
    schedule TEXT,
    syndicated BOOLEAN NOT NULL DEFAULT false,
    published_at DATE,
    modified_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ,
    fingerprint TEXT NOT NULL,
    duplicate_of BIGINT REFERENCES vacancy_details(event_id) ON DELETE SET NULL
);
CREATE INDEX vacancy_details_employer_idx ON vacancy_details (employer_key, published_at);
CREATE INDEX vacancy_details_active_idx ON vacancy_details (employer_key) WHERE closed_at IS NULL AND duplicate_of IS NULL;
CREATE INDEX vacancy_details_fingerprint_idx ON vacancy_details (fingerprint);
CREATE TABLE vacancy_employer_snapshots (
    employer_key TEXT NOT NULL REFERENCES vacancy_employers(employer_key),
    snapshot_date DATE NOT NULL,
    active_count INTEGER NOT NULL,
    active_work_places INTEGER NOT NULL,
    new_30d INTEGER NOT NULL,
    new_90d INTEGER NOT NULL,
    production_count INTEGER NOT NULL,
    engineering_count INTEGER NOT NULL,
    management_count INTEGER NOT NULL,
    municipalities TEXT[] NOT NULL DEFAULT '{}',
    new_municipalities TEXT[],          -- NULL: истории недостаточно
    categories JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (employer_key, snapshot_date)
);
```

Каждая вакансия — одна строка `source_events` (`vacancy_opened`, `dedup_key=id:<ID вакансии>`) плюс строка `vacancy_details` 1:1 с типизированными колонками для агрегатов. Заголовок, URL, ИНН и `raw` не дублируются.

### 6.5. Справочник категорий (`seed/vacancy_categories.json`)

```json
{
  "version": 1,
  "categories": [
    {"code": "technologist", "title": "Технологи", "profile": "engineering", "keywords": ["технолог"], "exclude": ["пищев\\w* технолог общепит"]},
    {"code": "engineer", "title": "Инженеры", "profile": "engineering", "keywords": ["инженер", "конструктор", "энергетик", "главный механик"]},
    {"code": "line_operator", "title": "Операторы производственных линий", "profile": "production", "keywords": ["оператор (производственной |технологической )?линии", "оператор станк", "оператор установ", "аппаратчик"]},
    {"code": "setter", "title": "Наладчики оборудования", "profile": "production", "keywords": ["наладчик", "настройщик"]},
    {"code": "production_head", "title": "Начальники производства", "profile": "production_management", "keywords": ["начальник (производства|цеха|участка)", "руководитель производства", "мастер (участка|цеха|смены)"]},
    {"code": "plant_director", "title": "Директора предприятий", "profile": "executive", "keywords": ["директор (завода|предприятия|производства|фабрики)", "технический директор"]},
    {"code": "worker", "title": "Рабочие производственных профессий", "profile": "production", "keywords": ["слесар", "сварщик", "токар", "фрезеровщик", "литейщик", "сборщик", "электромонтер", "станочник"]}
  ],
  "specialisationProfiles": {"Производство": "production", "Металлургия, металлообработка": "production"},
  "launchCombo": ["technologist", "engineer", "line_operator", "setter", "production_head", "plant_director"],
  "excludeEmployers": {
    "hrAgency": true,
    "specialisations": ["Государственная служба, некоммерческие организации", "Образование, наука", "Здравоохранение и социальное обеспечение"],
    "namePatterns": ["^(МБ|МА|МК|ГБ|ГА|ГК|ФГБ|ФГК|ФК)?(ОУ|ДОУ|УЗ|У)\\b", "администрац", "управлени\\w* (образования|социальной)"]
  }
}
```

- Ключевые слова — регулярные выражения по `title_norm` с границей слова. Порядок категорий задаёт приоритет. Если слова не сработали, используется профиль по `specialisation`, иначе `other`.
- Справочник валидируется pydantic-моделью при старте. Битый файл — ошибка запуска, а не молчаливый пропуск.
- Путь можно переопределить через `VACANCY_CATEGORIES_PATH`.
- Исключённые работодатели (`excluded_reason`) учитываются в счётчиках, но кадровые сигналы по ним не формируются.

### 6.6. Показатели по работодателю (`aggregates.py`)

Считаются по уникальным вакансиям (`duplicate_of IS NULL`) отдельно для каждого `employer_key` и для пары «работодатель + муниципалитет».

| Показатель | Определение |
| --- | --- |
| Актуальные вакансии | `closed_at IS NULL`; дополнительно сумма `work_places` |
| Новые за 30 / 90 дней | `published_at` (дата источника) в окне. Не `first_seen_at`: при первой выгрузке все вакансии «новые» для нас, но не для рынка |
| Динамика | `active_count` сейчас против снимка D−30 и D−90. Только если история наблюдения работодателя (`now − first_seen_at`) ≥ `minHistoryDays` и есть снимок на нужную дату; иначе `null` с причиной «недостаточно истории» |
| Производственный профиль | `profile = production` |
| Инженерный профиль | `profile = engineering` |
| Руководители производства | `profile IN (production_management, executive)` |
| Новый для компании муниципалитет | Муниципалитеты активных вакансий производственного, инженерного или управленческого профиля, где за предыдущие `newMunicipalityLookbackDays` наблюдения у работодателя не было вакансий. Пока история короче `minHistoryDays` — `null` |

Снимок `vacancy_employer_snapshots` пишется раз в сутки — это ряд для «динамики». Закрытие вакансий: раз в `TRUDVSEM_FULL_SYNC_DAYS` (7) выполняется полная выгрузка без `modifiedFrom`. Вакансии, которых в ней нет, получают `closed_at`, но только если все страницы выгрузились (`snapshot_complete`). Частичная выгрузка ничего не закрывает.

### 6.7. Кадровые сигналы (`signals.py`, `seed/hiring_signals.json`)

```json
{
  "minHistoryDays": 60,
  "newMunicipalityLookbackDays": 365,
  "massHiring": {"windowDays": 30, "baselineMultiplier": 3.0, "minAbsolute": 5, "noHistoryMinAbsolute": 15,
                 "tiers": [{"maxActive": 10, "minAbsolute": 5}, {"maxActive": 50, "minAbsolute": 12}, {"maxActive": null, "minAbsolute": 30}]},
  "growth": {"lookbackDays": 90, "minRatio": 1.5, "minDelta": 5},
  "productionLaunch": {"windowDays": 90, "minDistinctCategories": 3, "requireManagement": true},
  "newMunicipality": {"profiles": ["production", "engineering", "production_management", "executive"], "minVacancies": 2}
}
```

| Сигнал (`event_type`) | Условие | Требует истории |
| --- | --- | --- |
| `vacancy_opened` | Каждая вакансия | нет; к карточкам напрямую не привязывается |
| `vacancy_mass_hiring` | `new_30d ≥ max(floor(tier), baselineMultiplier × базовый месячный объём)`. Базовый объём — медиана новых вакансий в месяц за историю наблюдения. `tier` — по среднему `active_count` работодателя. Без истории — только `noHistoryMinAbsolute`, с отметкой «без базы сравнения» | частично |
| `vacancy_growth` | `active_now / active(D−90) ≥ minRatio` и прирост ≥ `minDelta` | да; без истории не рассчитывается |
| `vacancy_new_municipality` | Не меньше `minVacancies` вакансий нужных профилей в муниципалитете, новом для работодателя | да |
| `vacancy_production_launch` | За `windowDays` не меньше `minDistinctCategories` разных категорий из `launchCombo`, среди них хотя бы одна управленческая | нет |

Свойства сигнала:
- **Ключ.** `dedup_key = <тип>:<employer_key>:<муниципалитет|*>:<месяц начала эпизода>`. Пока условие держится, сигнал обновляется (новые показатели попадают в `source_event_versions`), а не плодится каждый день.
- **Содержимое.** В `raw` — показатели, использованные пороги, ID и ссылки до 50 вакансий-оснований, чтобы аналитик мог проверить. Поля сигнала: `inn`/`ogrn` работодателя (если есть), `source_company_id`, `company_name`, `municipality`, `metrics`; `url` — страница работодателя на trudvsem.
- **Без ИНН.** Сигнал уходит в `review` с причиной «нужно сопоставить работодателя с юрлицом» (правило 3 из 5.8).
- **Карточки.** Кадровые сигналы по умолчанию одни карточку не создают: нужна вторая категория событий (правило 4 из 5.8). Порог настраивается в `linking_rules.json`.

### 6.8. Адаптер `TrudvsemAdapter`

- **Запрос.** `fetch(cursor)` отправляет `GET {base}/vacancies/region/{regionCode}?offset={page}&limit={pageSize}` с `&modifiedFrom={cursor.modified_from}` при инкременте. Страницы идут, пока не наберётся `meta.total`, не придёт пустая страница или не будет достигнут `TRUDVSEM_MAX_PAGES` (статус `partial`).
- **Пауза** между страницами — `TRUDVSEM_PAGE_DELAY_SECONDS`.
- **Курсор.** `{"modified_from": <начало запуска − TRUDVSEM_OVERLAP_MINUTES>, "full_sync_at": ...}`. Полная выгрузка запускается, если с `full_sync_at` прошло больше `TRUDVSEM_FULL_SYNC_DAYS`.
- **Смещение списка.** Список меняется во время постраничного обхода, записи могут сдвинуться между страницами. Это закрывают перекрытие курсора, upsert по ID и еженедельная полная сверка.
- **Проверка** `check()`: один запрос с `limit=1`, ответ должен содержать `meta.total`.
- **Ошибки** — через `request_json` (5.3). Поменялась схема ответа (нет `results.vacancies`) → `SourceDataError`.
- **Настройки в `.env.example`:** `TRUDVSEM_BASE_URL=https://opendata.trudvsem.ru/api/v1`, `TRUDVSEM_REGION_CODE=6600000000000`, `TRUDVSEM_PAGE_DELAY_SECONDS=0.5`, `TRUDVSEM_MAX_PAGES=300`, `TRUDVSEM_FULL_SYNC_DAYS=7`, `TRUDVSEM_OVERLAP_MINUTES=60`, `VACANCY_CATEGORIES_PATH=`, `HIRING_SIGNALS_PATH=`.

### 6.9. Тесты П2

- **Получение вакансий** через MockTransport: проверяются путь, `regionCode`, `limit` и параметр страницы `offset`, нормализация полей, отсутствие `contact_person`, `contact_list` и `email` в `raw`.
- **Регион.** Фильтр по Свердловской области задаётся путём запроса; вакансия чужого региона в ответе (`region.region_code` ≠ 66…) отбрасывается и считается в `rejected`.
- **Пагинация.** Три страницы, `meta.total`, пустая страница, `TRUDVSEM_MAX_PAGES` даёт `partial`.
- **Дедупликация** по ID вакансии внутри страницы и между запусками.
- **Повторная синхронизация.** Без дублей, изменённая вакансия обновляется и получает версию.
- **Отсутствующий ИНН.** Ключ `ogrn:` или `trudvsem:<companycode>`, ИНН не выдумывается, сигнал уходит в `review`.
- **Ошибки API:** 500 с повтором, 429 с `Retry-After`, 503 подряд → `error`, битый JSON.
- **Агрегация** работодателей и все показатели на фиксированных датах (часы подменяются).
- **История.** Без неё `vacancy_growth` не формируется, и процент роста не выводится.
- **Сигналы:** массовый найм относительно собственной базы, комбинация запуска производства, новый муниципалитет, исключение кадровых агентств и бюджетных организаций.
- **Полная сверка:** закрывает пропавшие вакансии, частичная выгрузка не закрывает.

### 6.10. Внешние зависимости и доступы
Новых пакетов нет. Доступ к API открытый, без ключа (проверено). Нужно подтвердить условия использования открытых данных trudvsem.ru (лицензия набора) и вежливую частоту запросов.

---

## 7. Промпт 3. HeadHunter — отложен

Статус: не реализуется, пока нет подтверждённого доступа. README уже фиксирует, что поиск вакансий требует зарегистрированного приложения. Имитировать работающую интеграцию нельзя.

Когда доступ появится, задача ляжет на модуль из 6.2 без дублирования логики.

- **Создать:** `backend/app/sources/hh.py` (`HhAdapter`, `required_env=('HH_APP_TOKEN', 'HH_USER_AGENT')`), фикстуры, `tests/test_hh.py`. Без переменных окружения источник показывается со статусом «Требуется настройка», запросов не делает.
- **Проверить по актуальной документации** `api.hh.ru/openapi/redoc`:
  - идентификатор области (брать из `/areas`, не хардкодить);
  - фильтр по дате публикации;
  - `per_page` и ограничение глубины выдачи — при необходимости дробить запрос по датам;
  - обязательный заголовок `HH-User-Agent` с контактом команды (не личным);
  - наличие ИНН работодателя (вероятно, нет) и числа рабочих мест (вероятно, нет).
- **Межисточниковые дубли.** `fingerprint = sha1(employer_key | title_norm | municipality | вилка зарплаты, округлённая до 5 000)`. Совпадение с вакансией другого источника в пределах ±30 дней по `published_at` даёт `duplicate_of` на более раннюю. В показателях дубль не считается. Перепубликации «Работы России» (`syndicated=true`) — главные кандидаты.
- **Работодатели.** Без ИНН остаётся `hh:<employer_id>`. Связь с юрлицом — только вручную или через подтверждённые идентификаторы (П7). Автоматического объединения по названию нет.
- **PostgreSQL:** новых таблиц не нужно. `vacancy_employers.source_ids` и `vacancy_details.duplicate_of` уже есть.
- **Frontend:** карточка источника в группе «Кадровые источники».
- **Доступы:** регистрация приложения hh.ru и `client_id`/`client_secret` или токен приложения — только на бэкенде.

---

## 8. Промпт 4. ГИС Торги

- **Используем:** раннер П1, `upsert_events`, писатель расширений, импорт П6 как резервный путь, `geo`, `focus.valid_inn`.
- **Исследование до кода:** официальные «Открытые данные» torgi.gov.ru (наборы лотов за период и их формат), документированный API (есть ли он вообще), фильтр по субъекту 66, доступность победителей, ИНН победителя, кадастрового номера и договоров. Недокументированные JSON-эндпоинты фронтенда сайта считаются неофициальным скрейпингом — не используем. Если документированного канала нет, интеграция делается шаблоном импорта `torgi` (П6), ограничение фиксируется в README.
- **Создать:**
  - `backend/app/sources/torgi.py` (если канал есть);
  - `backend/app/land.py` — классификация ВРИ и типа имущества;
  - `seed/land_use_categories.json` — коды ВРИ по классификатору Росреестра: производственная деятельность (6.x), предпринимательство (4.x), жилая застройка (2.x), сельхоз (1.x);
  - `migrations/0NN_land_lots.sql`;
  - `tests/test_torgi.py`.
- **PostgreSQL:** `land_lot_details(event_id PK FK, notice_number, lot_number, bidding_type, property_type, vri_code, vri_text, land_use_class, area_m2, start_price, lease_years, organizer_name, organizer_inn, bid_end_at, winner_name, winner_inn)`.
- **События:**
  - `land_lot_published`, `land_lot_changed`, `land_lot_cancelled` — context: компания не привязывается;
  - `land_lot_awarded`, `land_contract_signed` — company, только по ИНН победителя или арендатора.
  - Ключ: `id:<извещение>:<лот>:<стадия>`. Изменение параметров видно через `source_event_versions`.
  - Жилая застройка (`land_use_class='residential'`) загружается, но не участвует в скоринге.
- **API:** общий `/api/events` с фильтром `type`, плюс `GET /api/events/{id}` показывает детали лота.
- **Frontend:** группа «Земля и недвижимость», детали лота в `EventsPage`.
- **Тесты:** загрузка, повторная обработка, смена статуса лота (версия и событие `land_lot_awarded`), лот без победителя не привязывается, жилая застройка отсекается.
- **Доступы:** подтверждение официального канала открытых данных и условий использования. При резерве — договорённость о выгрузках.

## 9. Промпт 5. ЕГРЗ и градостроительные процедуры

- **Исследование:** открытые данные ЕГРЗ (egrz.ru), поиск по Свердловской области, ИНН застройщика, результат экспертизы. Для ГИСОГД Свердловской области — есть ли публичный API или выгрузки и нужно ли соглашение. Разрешения на строительство и ввод, ГПЗУ, изменение ВРИ, ППТ — скорее всего только через ведомственный обмен, то есть импорт (П6). Опубликованное разрешение не доказывает доступа к истории заявлений.
- **Создать:** `backend/app/sources/egrz.py` (если канал подтверждён), `migrations/0NN_construction.sql`, шаблоны импорта `egrz` и `gisogd`, `tests/test_egrz.py`.
- **PostgreSQL:** `construction_details(event_id PK FK, conclusion_number, object_name, object_type, documentation_type, developer_name, developer_inn, received_at, issued_at, result ('positive' | 'negative'), is_repeat)`.
- **События:**
  - `construction_project_registered`;
  - `project_expertise` (положительное);
  - `project_expertise_negative` (negative, препятствие);
  - `project_expertise_repeat`;
  - `construction_project_changed`;
  - `construction_permit`, `commissioning_permit`, `gpzu_issued`, `land_use_changed`, `planning_documentation`.
  - Ключ — номер заключения. Застройщик определяется по ИНН; без ИНН событие идёт в `review`.
- **Frontend:** группа «Строительство».
- **Тесты:** извлечение сведений, идентификация застройщика, дедупликация, положительное, отрицательное и повторное заключение, изменение данных проекта.
- **Доступы:** подтверждение открытых данных ЕГРЗ; соглашение с Минстроем СО и оператором ГИСОГД — в перечень П10.

## 10. Промпт 6. Импорт данных региональных организаций

- **Используем:** `source_events` и `upsert_events` (П1), `focus.valid_inn`, `geo.canonical_municipality`, HTTP Basic, приём защиты от CSV-инъекций из `main.csv_text`.
- **Создать:**
  - `backend/app/imports.py`: разбор, валидация, staging, подтверждение;
  - `backend/seed/import_templates.json`: шаблоны `generic`, `rosseti`, `gas`, `water`, `heat`, `municipal`, `torgi`, `egrz`, `gisogd` — допустимые `event_type`, обязательные поля по типу, единицы измерения;
  - `common.valid_ogrn()`;
  - `migrations/0NN_imports.sql`;
  - `tests/test_imports.py`;
  - frontend: `components/ImportPanel.tsx`, блок «Импорт внешних данных» на `SourcesPage`.
- **PostgreSQL:**
  - `import_batches(id UUID PK, source_id, template, filename, sha256, size_bytes, uploaded_at, status ('validated' | 'confirmed' | 'cancelled' | 'failed'), total_rows, valid_rows, error_rows, duplicate_rows, inserted, updated, confirmed_at)`;
  - `import_rows(batch_id FK, row_number, data JSONB, errors JSONB, status ('valid' | 'error' | 'duplicate'), dedup_key, PRIMARY KEY (batch_id, row_number))`.
  - В `source_events` строки попадают только при подтверждении, одной транзакцией.
- **API:**
  - `GET /api/imports/templates`;
  - `GET /api/imports/templates/{code}.csv`;
  - `POST /api/imports` (multipart: `sourceId`, `template`, файл);
  - `GET /api/imports`;
  - `GET /api/imports/{id}?rows=errors|all` (предпросмотр и ошибки по строкам);
  - `POST /api/imports/{id}/confirm`;
  - `POST /api/imports/{id}/cancel`.
- **Валидация:**
  - структура и заголовки шаблона, незнакомые колонки отклоняются;
  - контрольные суммы ИНН и ОГРН;
  - даты в форматах ISO и `ДД.ММ.ГГГГ`;
  - числа (запятая как разделитель допустима, единицы из списка шаблона);
  - муниципалитет по справочнику `geo` (неизвестный — ошибка строки);
  - кадастровый номер `^[0-9]{2}:[0-9]{2}:[0-9]{6,7}:[0-9]+$`;
  - URL только `http(s)`;
  - длины строк;
  - дубли внутри файла и с уже загруженными (`dedup_key`).
  - Для Россетей отдельно: ИНН заявителя, адрес, кадастровый номер, мощность (кВт), статус, причина отказа, даты подачи и плановая дата подключения.
- **Безопасность:**
  - `IMPORT_MAX_BYTES` (5 МБ) — проверка до чтения целиком;
  - `IMPORT_MAX_ROWS` (20 000);
  - ограниченный `csv.field_size_limit`;
  - кодировка utf-8-sig с откатом на cp1251;
  - разделитель только `;` или `,`;
  - в nginx `client_max_body_size 6m` для `/api/imports` (B12);
  - персональные данные физлиц шаблонами не предусмотрены;
  - повторная загрузка того же файла (совпал sha256) показывает прежний пакет, а повторное подтверждение идемпотентно.
- **Источники:** в `seed/sources.json` записи `kind='import'` (`scheduled=false`, `connection='manual_only'`): «Россети Урал», «Газоснабжение», «Водоснабжение», «Теплоснабжение», «Муниципалитеты», «Агентство по привлечению инвестиций», «Минстрой СО», «ГИСОГД».
- **Типы событий:**
  - `engineering_connection`, `engineering_capacity_increase`;
  - `engineering_tu_issued`, `engineering_contract_signed`, `engineering_status_changed`, `engineering_connected`;
  - `engineering_refusal`, `engineering_contract_terminated`, `engineering_delay`;
  - `land_application`, `urban_planning_document`.
- **Frontend:** шаблоны для скачивания, загрузка, предпросмотр, ошибки по строкам, подтверждение и отмена, последние импорты со счётчиками загруженных записей, ошибок, дублей и результатом.
- **Тесты:**
  - валидный файл;
  - ошибки по строкам (ИНН, дата, число, муниципалитет);
  - слишком большой файл и слишком много строк;
  - cp1251;
  - строки с `=`, `+`, `-`, `@` хранятся как текст;
  - повторная загрузка того же файла;
  - до подтверждения в `source_events` ничего нет;
  - отмена;
  - доступ без входа — 401.
- **Зависимости:** `python-multipart` (официальная зависимость FastAPI для `UploadFile`), версию закрепить в `requirements.txt` при реализации. Доступы — соглашения с организациями о передаче данных (перечень в П10).

## 11. Промпт 7. Идентификация компаний и проекты

- **Используем:** `candidate_events`, `inn_key` (П1), `vacancy_employers` (П2), `focus.FocusClient` (только с подтверждённым ИНН), `pipeline.find_candidate` и `attach_evidence`.
- **Создать:** `backend/app/organizations.py`, `backend/app/projects.py`, `migrations/0NN_organizations.sql`, `tests/test_projects.py`.
- **PostgreSQL:**
  - `organizations(id PK, inn UNIQUE, ogrn UNIQUE, name, short_name, created_at)`;
  - `organization_identifiers(scheme ('inn' | 'ogrn' | 'trudvsem' | 'hh' | 'torgi' | 'import' | ...), value, organization_id FK, verified BOOLEAN, source, created_at, PRIMARY KEY (scheme, value))`;
  - `organization_match_suggestions(id, left_key, right_key, reason, status)` — сходство названий только предлагается аналитику;
  - `candidate_history(id, candidate_number FK, at, actor ('system' | 'analyst' | 'assistant'), action, from_value, to_value, reason, event_id)`;
  - в `payload` карточки необязательный объект `project {organizationId, municipality, address, cadastralNumbers[], projectType, objectName}` и генерируемые колонки `organization_key` и `municipality_key` для индексов.
- **Логика:**
  - ИНН — главный ключ юрлица. ОГРН и внешние ID связываются через `organization_identifiers` только при подтверждении: совпал ИНН в том же источнике или решение аналитика.
  - Проект = организация + площадка. Совпали кадастровый номер или нормализованный адрес → `confirmed`. Совпали муниципалитет и тип проекта → `probable`. Другой муниципалитет или противоречие → `manual_review`. Совпадения одного ИНН недостаточно.
  - Неуверенные совпадения автоматически не объединяются.
  - `pipeline.find_candidate(company, inn, municipality)` возвращает уровень совпадения. Новость с известным ИНН сравнивается по ИНН. Если ИНН нет, используется прежнее совпадение названия, но при другом муниципалитете новость не склеивается, а уходит в `review`. Флаг `NEWS_MERGE_STRICT=1`, по умолчанию 1. Это меняет RSS-разбор, поэтому нужны отдельные тесты.
  - Расхождение ИНН у карточки (B6) отправляет её в `review`.
- **API:**
  - `GET /api/organizations/{inn}`, `GET /api/organizations/{inn}/projects`;
  - `GET /api/candidates/{id}/related-events` — кандидаты на связь;
  - `POST /api/events/{id}/link {candidateId}`, `DELETE /api/events/{id}/link/{candidateId}`;
  - `POST /api/events/{id}/review {action: ignore | new_card}`;
  - `POST /api/candidates/{id}/merge {otherId}`, `POST /api/candidates/{id}/split {eventIds}`;
  - `GET /api/candidates/{id}/history`.
- **Frontend:** очередь `review` в `EventsPage`, кнопки «Связать», «Отделить» и «Объединить» в `CandidateDialog`, история изменений.
- **Тесты:**
  - три проекта одного ИНН в разных муниципалитетах дают три карточки;
  - вакансии, затем земля, затем мощность на одной площадке попадают в одну карточку;
  - похожие названия не склеиваются;
  - Фокус не вызывается без ИНН;
  - объединение и разделение с историей;
  - старые карточки не меняются.
- **Доступы:** не нужны. Поиск ИНН по названию (DaData и т. п.) — отдельное решение команды, в ТЗ не требуется.

## 12. Промпт 8. Инвестиционные намерения и препятствия

- **Используем:** события и связи (П1, П7), `event_types` (`category`, `polarity`), `llm.LlmClient` (бюджет, `chat_json`), `risk` (отдельно, не смешивать).
- **Создать:** `backend/app/scoring/{config.py, engine.py, explain.py}`, `seed/scoring.json`, `tests/test_scoring.py`, при необходимости `migrations/0NN_assessment.sql` (генерируемые колонки для фильтров).
- **Модель:** чистая функция `assess(events, now, config) -> Assessment`.
  - **Оценки:** инвестиционный потенциал, достоверность доказательств, стадия реализации, региональный эффект, срочность контакта, серьёзность препятствий. Каждая 0–100 и подаётся как предварительная аналитическая оценка, а не вероятность.
  - **Вклад события** = вес типа × надёжность источника × затухание `0.5^(возраст/halfLife категории)`.
  - **Без повторных баллов.** Один и тот же факт (`duplicate_of`, одна вакансия в двух источниках, одна новость в нескольких СМИ) учитывается один раз. Бонус дают число независимых источников и связанных событий, а также географическая согласованность.
  - **Препятствия** (отказ в присоединении, нехватка мощности, долгие сроки, отказ в земле, отрицательная экспертиза, расторжение аренды, явные признаки переноса в другой регион) считаются отдельно. Отсутствие новых событий препятствием не считается.
  - **Категории лида:** новый проект, расширение, модернизация, поиск площадки, подготовка строительства, создание рабочих мест, инфраструктурные препятствия, риск отказа, ручная проверка.
- **Хранение:** `payload.assessment` = `{scores, stage, leadCategories, factors: [{eventId, code, weight, decay, contribution}], barriers: [...], computedAt, configVersion}`. Старые `factors` и `score` не трогаем. Пересчёт выполняется при изменении связей и раз в сутки (затухание).
- **ИИ-пояснение:** GigaChat, `purpose='explain'`. JSON Schema `{facts: [{text, eventIds}], stage, barriers, ministryHelp}`.
  - Факт без `eventIds`, ссылка на несуществующий ID или число, которого нет в событиях, отбрасываются.
  - Названия компаний сверяются с организацией карточки.
  - Вызов только по кнопке или для новых карточек в пределах бюджета.
- **API:** `GET /api/candidates/{id}/assessment`, `POST /api/candidates/{id}/explain`, `GET /api/scoring/config` (только чтение).
- **Тесты:**
  - только кадры;
  - земля + мощность + кадры;
  - отказ в присоединении → категория «инфраструктурные препятствия»;
  - затухание старых событий;
  - один факт из двух источников;
  - красная зона Фокуса не меняет потенциал;
  - ИИ-пояснение с несуществующим `eventId` отбрасывается.
- **Доступы:** бюджет GigaChat (в тестах только MockTransport).

## 13. Промпт 9. Интерфейс

- **Изменить:** `SourcesPage.tsx`, `QueuePage.tsx`, `CandidateDialog.tsx`, `MapPage.tsx`, `ProjectMap.tsx`, `AnalyticsPage.tsx`, `domain/selectors.ts`, `domain/models.ts`, `services/repository.ts`, `app/Shell.tsx`, `agent.py` (инструменты `search_events` и `get_candidate_events`, только чтение).
- **Создать:**
  - `pages/EventsPage.tsx` — полная версия;
  - `components/EventTimeline.tsx`, `IntentFactors.tsx`, `BarriersPanel.tsx`, `SourceGroup.tsx`;
  - `playwright.config.ts` и `e2e/*.spec.ts` (B9).
- **Источники:**
  - группы из `group`;
  - статус подключения, последняя синхронизация, события, сигналы, ошибки, ручной запуск, ссылка на первоисточник;
  - отметка «нужен ключ или доступ»;
  - неподключённый источник не показывается работающим.
- **Очередь:** новые фильтры (тип проекта, препятствия, несколько независимых подтверждений, источник сигнала, муниципалитет, стадия, инвестиционный рейтинг) поверх существующих. Старые параметры URL продолжают работать.
- **Карточка:**
  - «История инвестиционной активности» из `/api/candidates/{id}/events` (сортировка по `occurredAt`, у старых доказательств — по дате карточки);
  - «Признаки инвестиционного намерения» из `assessment.factors`;
  - «Препятствия реализации проекта»;
  - блок Контур.Фокуса без изменений.
- **Карта:** точность `project` / `address` / `municipality` с подписью «приблизительно — центр муниципалитета», фильтры по типу проекта и препятствиям.
- **Аналитика:**
  - `GET /api/analytics/live` считается по БД: события, компании с сигналами, проекты с несколькими подтверждениями, разрезы по муниципалитетам, отраслям, стадиям и препятствиям, результативность источников;
  - пустая база — нулевое состояние;
  - демо-показатели либо подписаны как пример, либо скрыты (B11).
- **Тесты:** Vitest на селекторы и схемы; Playwright smoke: вход, очередь, карточка, источники, события, пустые состояния, axe.
- **Зависимости:** уже есть в `devDependencies` (`@playwright/test`, `@axe-core/playwright`). Браузер ставится через `npm run test:browser:install`.

## 14. Промпт 10. Финальная интеграция

- Полный прогон: миграции на копии рабочей БД (старые карточки), RSS, Дзен (мок), новые источники, worker, повторные загрузки, импорт, скоринг, Фокус (мок), ассистент, CSV.
- Безопасность:
  - `git grep` по ключам и токенам;
  - закрытость всех `/api/*`;
  - лимиты размеров;
  - валидация ИНН;
  - лимиты частоты внешних API;
  - CSV-инъекции в экспорте и импорте.
- README: архитектура, источники и их настройка, ключи и переменные, формат импорта, идентификация, объединение, скоринг, препятствия, worker, тесты, ограничения доступа. `DEPLOYMENT.md` создать или убрать ссылку (B2).
- Отдельный перечень источников, для которых нужны соглашения: Россети Урал, газо-, водо- и теплоснабжающие организации, Минстрой СО, ГИСОГД, муниципалитеты, Агентство по привлечению инвестиций, hh.ru, а также ГИС Торги и ЕГРЗ, если не подтвердится открытый канал.
- Итоговый отчёт по форме ТЗ. Подготовленное, но не подключённое называем «ожидает доступа», а не «реализовано».

---

## 15. Архитектурные риски

### 15.1. Дублирование компаний
- **Сейчас.** Карточки ищутся только по нормализованному названию (B5), ИНН не участвует. «ЕЗ ОЦМ» и «Екатеринбургский завод по обработке цветных металлов» дают две карточки. `normalize_company` выбрасывает «завод» и «компания» и склеивает разные фирмы.
- **В новых источниках.** У одного юрлица несколько написаний (trudvsem, импорт, новости). Филиалы имеют тот же ИНН, но другой КПП и муниципалитет. Холдинг — это несколько ИНН. При реорганизации ИНН меняется. У HH ИНН нет вовсе.
- **Меры:**
  - ИНН с контрольной суммой — главный ключ;
  - внешние ID связываются только через подтверждённые идентификаторы;
  - похожие названия, адреса и учредители только предлагаются аналитику;
  - генерируемая колонка `inn_key` и таблица `organizations`;
  - фикс B3 (выдуманный ИНН);
  - Фокус вызывается только с подтверждённым ИНН.

### 15.2. Объединение разных проектов одной компании
- **Сейчас.** Все новости о компании попадают в одну карточку независимо от площадки.
- **Риск.** После П1 связь по ИНН без учёта площадки сольёт «новый завод в Первоуральске» и «склад в Нижнем Тагиле».
- **Меры:**
  - ключ проекта = организация + площадка (кадастровый номер, адрес, муниципалитет) + тип проекта;
  - уровни `confirmed` / `probable` / `manual_review`;
  - при расхождении муниципалитета — `review`;
  - события уровня компании (массовый найм без муниципалитета) не привязываются автоматически к одному из нескольких проектов;
  - ручное объединение и разделение с историей;
  - связь многие-ко-многим в `candidate_events`.

### 15.3. Повторная загрузка событий
- **Риски:**
  - повторный запуск;
  - перекрытие инкрементального окна;
  - смещение страниц при постраничном обходе меняющегося списка;
  - повторный импорт того же CSV;
  - одна вакансия в двух источниках (у trudvsem есть перепубликации «Вакансия интернет ресурса»);
  - ежедневный пересчёт сигналов;
  - первая выгрузка, когда всё выглядит «новым»;
  - параллельный ручной и плановый запуск.
- **Меры:**
  - `UNIQUE (source_id, dedup_key)` и upsert с `content_hash`;
  - версии вместо новых строк;
  - курсор двигается только после успешного запуска;
  - полная сверка закрывает пропавшие записи только после полной выгрузки;
  - sha256 файла и идемпотентное подтверждение импорта;
  - `fingerprint` и `duplicate_of` для межисточниковых дублей;
  - стабильные ключи эпизодов сигналов;
  - «новизна» считается по датам источника, а не по `first_seen_at`;
  - advisory lock на источник;
  - скоринг считает уникальные факты.

### 15.4. Ложное определение инвестиционного намерения
- **Вакансии.** Отражают текучесть и сезонность. В выборке trudvsem около 45% — бюджетная сфера и ЖКХ, кадровые агентства публикуют чужие вакансии.
- **Меры по вакансиям:**
  - исключение бюджетных организаций и агентств;
  - пороги относительно собственной базы работодателя;
  - рост не считается без истории;
  - одни кадровые сигналы карточку не создают.
- **Земля.** Публикация лота не означает интереса конкретной компании: компания связывается только по победителю или договору.
- **Экспертиза и разрешения.** По жилью отсекаются по типу объекта и ВРИ.
- **Негативные выводы.** «Уход из региона» по отсутствию событий запрещён.
- **ИИ.** Пояснения проверяются по `eventId` и числам. Выдуманный ИНН — B3.
- **Фокус.** Зона Фокуса не смешивается с потенциалом.
- **Калибровка.** Все пороги и веса вынесены в seed-конфиги. Отклонения аналитика сохраняются для калибровки, перед включением пороги проверяются на размеченной выборке.

### 15.5. Прочие риски
- **Совместимость frontend.** Жёсткие enum в Zod (B8): менять бэкенд и frontend в одном коммите, проверять старый и новый `/api/state` в тестах.
- **Рост `payload JSONB`.** Фильтры по ИНН, муниципалитету, рейтингу и статусу выполняются в Python. Нужны генерируемые колонки и индексы (`inn_key`, затем `organization_key`, `municipality_key`, `investment_potential`), а `/api/state` со временем — пагинация.
- **Лимит Фокуса.** Автопроверка ИНН из событий исчерпает 950 компаний: `FOCUS_AUTO_CHECK_EVENTS=0`, проверяются только карточки.
- **Однопоточный worker.** Полная выгрузка trudvsem (около 165 запросов с паузами) задерживает RSS и разбор: отдельный сервис `collector`, ручной запуск через очередь.
- **Ограничения миграций (B13).** Без `;` и ` :имя` внутри SQL. Номера миграций назначаются при слиянии, иначе ветки конфликтуют.
- **Внешние API.** Условия могут меняться: документация trudvsem на dev-стенде, лимиты частоты не опубликованы, у HH закрытый доступ. Нужны `check()`, статус «нет доступа», вежливые паузы, обработка 429 и `Retry-After`.
- **Персональные данные.** У trudvsem в ответе ФИО и контакты: `raw` строится только по белому списку. Импорт не принимает колонки с данными физлиц. Фикстуры обезличены.
- **Демо-артефакты в рабочих данных** (B10, B11): разделить демо и реальную аналитику, не создавать фиктивных сигналов.

---

## 16. Сводка новых переменных окружения

| Переменная | По умолчанию | Задача |
| --- | --- | --- |
| `SOURCE_RETRY_ATTEMPTS`, `SOURCE_BACKOFF_BASE_MINUTES`, `SOURCE_MAX_RETRY_AFTER`, `SOURCE_RUN_STALE_MINUTES` | `3`, `5`, `300`, `60` | П1 |
| `EVENT_LINK_BATCH`, `FOCUS_AUTO_CHECK_EVENTS` | `200`, `0` | П1 |
| `TRUDVSEM_BASE_URL`, `TRUDVSEM_REGION_CODE` | `https://opendata.trudvsem.ru/api/v1`, `6600000000000` | П2 |
| `TRUDVSEM_PAGE_DELAY_SECONDS`, `TRUDVSEM_MAX_PAGES`, `TRUDVSEM_FULL_SYNC_DAYS`, `TRUDVSEM_OVERLAP_MINUTES` | `0.5`, `300`, `7`, `60` | П2 |
| `VACANCY_CATEGORIES_PATH`, `HIRING_SIGNALS_PATH` | пусто (берутся seed-файлы) | П2 |
| `HH_APP_TOKEN` (или `HH_CLIENT_ID`/`HH_CLIENT_SECRET`), `HH_USER_AGENT` | пусто, источник «Требуется настройка» | П3 |
| `IMPORT_MAX_BYTES`, `IMPORT_MAX_ROWS` | `5242880`, `20000` | П6 |
| `NEWS_MERGE_STRICT` | `1` | П7 |
| `SCORING_CONFIG_PATH`, `LLM_EXPLAIN_ON_CREATE` | пусто, `0` | П8 |

## 17. Вопросы к команде

1. П3 (HeadHunter) отложен — подтвердить, что реализуем только «Работу России».
2. Порог создания карточки из событий (по умолчанию две разные категории событий в одном муниципалитете за 180 дней) — согласовать до П1.
3. Бюджетные организации в кадровом мониторинге исключаем по умолчанию. Нужны ли отдельно госкорпорации и муниципальные унитарные предприятия как инвесторы?
4. Демо-имя «А. Крылова» при одобрении (B10) убрать или заменить выбором ответственного?
5. Сервис `collector` в `compose.yaml` — согласовать с тем, как развёрнут сервер (Docker в рамках этих задач не запускаем).
