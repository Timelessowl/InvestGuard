import { useDeferredValue } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowDownUp,
  ArrowUpRight,
  Search,
  SlidersHorizontal,
  Clock3,
  Zap,
  FileCheck2,
  CheckCheck,
  X,
  Building2,
} from "lucide-react";
import { useApp } from "../app/AppProvider";
import { statusLabels, statusSchema, type Candidate } from "../domain/models";
import {
  filterCandidates,
  projectCount,
  queueMetrics,
  type SortOrder,
} from "../domain/selectors";
import { StatCard, StatusBadge } from "../components/ui";
import { CandidateDialog } from "../components/CandidateDialog";

function Score({ value }: { value: number }) {
  return (
    <span className={`score ${value >= 80 ? "score-high" : ""}`}>
      <strong>{value}</strong>
      <span className="score-track" aria-hidden="true">
        <span style={{ width: `${value}%` }} />
      </span>
    </span>
  );
}
function Urgency({ candidate }: { candidate: Candidate }) {
  return (
    <span className={`urgency urgency-${candidate.urgency}`}>
      <span aria-hidden="true" />
      {candidate.urgencyLabel}
    </span>
  );
}
export default function QueuePage() {
  const { candidates, busy } = useApp();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const deferredQuery = useDeferredValue(query);
  const rawStatus = params.get("status");
  const status =
    rawStatus === "pending"
      ? "pending"
      : statusSchema.safeParse(rawStatus).success
        ? statusSchema.parse(rawStatus)
        : "all";
  const sector = params.get("sector") ?? "all";
  const sort: SortOrder =
    params.get("sort") === "score-asc"
      ? "score-asc"
      : params.get("sort") === "company"
        ? "company"
        : "score-desc";
  const urgent = params.get("urgent") === "1";
  const filtered = filterCandidates(candidates, {
    query: deferredQuery,
    status,
    sector,
    sort,
    urgent,
  });
  const metrics = queueMetrics(candidates);
  const selected = candidates.find(
    (candidate) => candidate.id === params.get("case"),
  );
  const sectors = [
    ...new Set(candidates.map((candidate) => candidate.sector)),
  ].sort((a, b) => a.localeCompare(b, "ru"));
  const activeFilters = Boolean(
    query || status !== "all" || sector !== "all" || urgent,
  );
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (!value || value === "all" || (key === "sort" && value === "score-desc"))
      next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
  const open = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("case", id);
    setParams(next);
  };
  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("case");
    setParams(next, { replace: true });
  };
  const focusQueue = (filter: string, high = false) => {
    const next = new URLSearchParams();
    next.set("status", filter);
    if (high) next.set("urgent", "1");
    setParams(next, { replace: true });
  };
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">Обнаружить. Проверить. Действовать.</span>
          <h1>Очередь проверки</h1>
          <p>От первых сигналов — к подтверждённым инвестиционным проектам.</p>
        </div>
        <span className="context-tag">
          <Building2 size={16} aria-hidden="true" />
          Свердловская область
        </span>
      </div>
      <div className="stats-grid">
        <StatCard
          label="Ожидают решения"
          value={metrics.open.toString().padStart(2, "0")}
          note="Новые и на проверке"
          icon={<Clock3 size={20} aria-hidden="true" />}
          accent
          onClick={() => focusQueue("pending")}
        />
        <StatCard
          label="Высокий приоритет"
          value={metrics.urgent.toString().padStart(2, "0")}
          note="Открытые проекты · реакция до 2 дней"
          icon={<Zap size={20} aria-hidden="true" />}
          onClick={() => focusQueue("pending", true)}
        />
        <StatCard
          label="Готовы к экспорту"
          value={metrics.approved.toString().padStart(2, "0")}
          note="Подтверждены экспертом"
          icon={<FileCheck2 size={20} aria-hidden="true" />}
          onClick={() => focusQueue("approved")}
        />
        <StatCard
          label="Экспортированы"
          value={metrics.exported.toString().padStart(2, "0")}
          note="Выгружены в CSV"
          icon={<CheckCheck size={20} aria-hidden="true" />}
          onClick={() => focusQueue("exported")}
        />
      </div>
      <section className="panel queue-panel" aria-labelledby="queue-title">
        <div className="panel-heading">
          <div>
            <h2 id="queue-title">
              Приоритетные проекты{" "}
              <span className="count-pill">{candidates.length}</span>
            </h2>
            <p>Проверяйте доказательства и выбирайте следующий шаг.</p>
          </div>
          <span className="small-label">
            <SlidersHorizontal size={15} aria-hidden="true" />
            Рабочая очередь
          </span>
        </div>
        <div className="filters">
          <div className="field search-field">
            <label htmlFor="queue-search">Поиск</label>
            <div className="input-with-icon">
              <Search size={18} aria-hidden="true" />
              <input
                id="queue-search"
                type="search"
                value={query}
                onChange={(event) => setFilter("q", event.target.value)}
                placeholder="Компания, сигнал или город"
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="queue-status">Статус</label>
            <select
              id="queue-status"
              value={status}
              onChange={(event) => setFilter("status", event.target.value)}
            >
              <option value="all">Все статусы</option>
              <option value="pending">Ожидают решения</option>
              {Object.entries(statusLabels).map(([key, label]) => (
                <option value={key} key={key}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="queue-sector">Отрасль</label>
            <select
              id="queue-sector"
              value={sector}
              onChange={(event) => setFilter("sector", event.target.value)}
            >
              <option value="all">Все отрасли</option>
              {[
                ...new Set([...sectors, ...(sector !== "all" ? [sector] : [])]),
              ].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </div>
          <div className="field sort-field">
            <label htmlFor="queue-sort">Сортировка</label>
            <select
              id="queue-sort"
              value={sort}
              onChange={(event) => setFilter("sort", event.target.value)}
            >
              <option value="score-desc">Приоритет: сначала высокий</option>
              <option value="score-asc">Приоритет: сначала низкий</option>
              <option value="company">Компания: А–Я</option>
            </select>
          </div>
        </div>
        <div className="results-strip">
          <span aria-live="polite" role="status">
            {projectCount(filtered.length)}
            {activeFilters ? ` из ${candidates.length}` : ""}
          </span>
          <div className="inline-actions">
            {urgent && (
              <button
                className="filter-chip"
                onClick={() => setFilter("urgent", "")}
              >
                Высокий приоритет <X size={14} aria-hidden="true" />
              </button>
            )}
            {activeFilters && (
              <button
                className="text-button"
                onClick={() =>
                  setParams(
                    new URLSearchParams(
                      sort === "score-desc" ? "" : `sort=${sort}`,
                    ),
                    { replace: true },
                  )
                }
              >
                Сбросить фильтры
              </button>
            )}
          </div>
        </div>
        {filtered.length ? (
          <>
            <div className="desktop-table">
              <table>
                <caption className="sr-only">
                  Инвестиционные проекты в очереди проверки
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Компания / проект</th>
                    <th scope="col">Сигнал</th>
                    <th
                      scope="col"
                      aria-sort={
                        sort === "score-desc"
                          ? "descending"
                          : sort === "score-asc"
                            ? "ascending"
                            : "none"
                      }
                    >
                      <button
                        className="table-sort"
                        onClick={() =>
                          setFilter(
                            "sort",
                            sort === "score-desc" ? "score-asc" : "score-desc",
                          )
                        }
                      >
                        Оценка <ArrowDownUp size={14} aria-hidden="true" />
                      </button>
                    </th>
                    <th scope="col">Срочность</th>
                    <th scope="col">Статус</th>
                    <th scope="col">Ответственный</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((candidate) => (
                    <tr key={candidate.id} onClick={() => open(candidate.id)}>
                      <td>
                        <div className="company-cell">
                          <span className="company-avatar" aria-hidden="true">
                            {candidate.initials}
                          </span>
                          <div>
                            <button
                              className="company-link"
                              onClick={(event) => {
                                event.stopPropagation();
                                open(candidate.id);
                              }}
                            >
                              {candidate.company}
                              <ArrowUpRight size={14} aria-hidden="true" />
                            </button>
                            <span className="table-secondary">
                              {candidate.city} · {candidate.sector}
                            </span>
                            <span className="case-id">{candidate.id}</span>
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="signal-cell">
                          <strong>{candidate.signal}</strong>
                          <span className="table-secondary">
                            {candidate.source}
                          </span>
                          <span className="case-id">
                            {candidate.sourceDate}
                          </span>
                        </div>
                      </td>
                      <td>
                        <Score value={candidate.score} />
                      </td>
                      <td>
                        <Urgency candidate={candidate} />
                      </td>
                      <td>
                        <StatusBadge status={candidate.status} />
                      </td>
                      <td>
                        <span
                          className={`owner ${candidate.owner === "Не назначен" ? "owner-empty" : ""}`}
                        >
                          {candidate.owner}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="mobile-candidates">
              {filtered.map((candidate) => (
                <li key={candidate.id}>
                  <button
                    className="candidate-card"
                    onClick={() => open(candidate.id)}
                  >
                    <span className="candidate-card-top">
                      <span className="company-avatar" aria-hidden="true">
                        {candidate.initials}
                      </span>
                      <span>
                        <strong>{candidate.company}</strong>
                        <span className="table-secondary">
                          {candidate.city}
                        </span>
                      </span>
                      <ArrowUpRight size={20} aria-hidden="true" />
                    </span>
                    <span className="mobile-signal">{candidate.signal}</span>
                    <span className="candidate-card-bottom">
                      <StatusBadge status={candidate.status} />
                      <span className="mobile-score">
                        Приоритет <strong>{candidate.score}/100</strong>
                      </span>
                    </span>
                    <span className="candidate-card-bottom">
                      <Urgency candidate={candidate} />
                      <span className="case-id">{candidate.id}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <div className="empty-state">
            <Search size={30} aria-hidden="true" />
            <h3>Подходящих проектов нет</h3>
            <p>Попробуйте другой запрос или сбросьте фильтры.</p>
            <button
              className="button"
              onClick={() =>
                setParams(new URLSearchParams(), { replace: true })
              }
            >
              Показать все проекты
            </button>
          </div>
        )}
        <div className="table-footer">
          <ShieldNote />
          <span>Оценка от 0 до 100 · выше — приоритетнее</span>
        </div>
      </section>
      {params.has("case") && !selected && (
        <div className="inline-error" role="alert">
          Карточка не найдена.
          <button className="text-button" onClick={close}>
            Вернуться к очереди
          </button>
        </div>
      )}
      {selected && (
        <CandidateDialog
          key={selected.id}
          candidate={selected}
          onClose={close}
        />
      )}
      <span className="sr-only" role="status">
        {busy ? "Сохраняем изменения" : ""}
      </span>
    </>
  );
}
function ShieldNote() {
  return <span>Каждое решение — после проверки источников</span>;
}
