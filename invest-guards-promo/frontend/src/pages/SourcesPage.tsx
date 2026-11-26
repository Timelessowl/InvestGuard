import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Newspaper,
  RotateCw,
  CircleCheck,
  TriangleAlert,
  ExternalLink,
  Play,
  Database,
  CircleSlash,
  PlugZap,
} from "lucide-react";
import { useApp } from "../app/AppProvider";
import { track } from "../services/analytics";
import {
  connectionLabels,
  documentStageLabels,
  documentStages,
  sourceStatusLabels,
  type CollectedDocument,
  type DataSource,
  type DocumentStage,
} from "../domain/models";
import { isWorkingSource } from "../domain/selectors";
import { Panel, Spinner, StatCard, BarChart } from "../components/ui";

const stageBadge: Record<DocumentStage, string> = {
  new: "status-new",
  skipped: "status-exported",
  rejected: "status-rejected",
  signal: "status-approved",
  merged: "status-review",
  error: "status-rejected",
};
function SourceHealth({ source }: { source: DataSource }) {
  // A source that is not connected is never shown as working.
  const label =
    source.status !== "disabled" && source.connection !== "ready"
      ? connectionLabels[source.connection]
      : sourceStatusLabels[source.status];
  const tone = isWorkingSource(source) ? "health-active" : source.status === "retry" ? "health-warning" : "health-muted";
  return (
    <span className={`source-health ${tone}`}>
      {isWorkingSource(source) ? (
        <CircleCheck size={15} aria-hidden="true" />
      ) : source.status === "retry" ? (
        <TriangleAlert size={15} aria-hidden="true" />
      ) : (
        <CircleSlash size={15} aria-hidden="true" />
      )}
      {label}
    </span>
  );
}
export default function SourcesPage() {
  const { sources, pipeline, api, mutate, busy, notify } = useApp();
  const [collecting, setCollecting] = useState<string | null>(null);
  const [checking, setChecking] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);
  const [stage, setStage] = useState<DocumentStage | "all">("all");
  const [documents, setDocuments] = useState<CollectedDocument[] | null>(null);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const stages = pipeline?.stages ?? {};
  const usage = pipeline?.usage;
  const totalDocuments = Object.values(stages).reduce((sum, value) => sum + value, 0);
  const signals = (stages.signal ?? 0) + (stages.merged ?? 0);
  const active = sources.filter(isWorkingSource).length;
  const sourceNames = Object.fromEntries(sources.map((source) => [source.id, source.name]));
  useEffect(() => {
    let current = true;
    api
      .documents(stage === "all" ? undefined : stage)
      .then((result) => {
        if (!current) return;
        setDocuments(result);
        setDocumentsError(null);
      })
      .catch((error: unknown) => {
        if (current) setDocumentsError(error instanceof Error ? error.message : "Не удалось загрузить документы.");
      });
    return () => {
      current = false;
    };
  }, [api, stage, totalDocuments, signals]);
  async function collect(id: string) {
    setCollecting(id);
    try {
      const result = await mutate(() => api.collectSource(id));
      const ok = result.queued || result.source.status === "active";
      track("source_collect", { source: id, added: result.added, ok });
      notify(
        result.queued
          ? `${result.source.name}: сбор поставлен в очередь, данные появятся после выгрузки`
          : result.source.status === "active"
            ? `${result.source.name}: новых ${result.source.produces === "events" ? "событий" : "документов"} — ${result.added}`
            : `${result.source.name}: сбор не удался (${result.source.lastError ?? "ошибка"})`,
        ok ? "success" : "error",
      );
    } catch {
      /* Error is announced by provider. */
    } finally {
      setCollecting(null);
    }
  }
  async function check(id: string, name: string) {
    setChecking(id);
    try {
      const result = await mutate(() => api.checkSource(id));
      notify(`${name}: ${result.message}`, result.ok ? "success" : "error");
    } catch {
      /* Error is announced by provider. */
    } finally {
      setChecking(null);
    }
  }
  async function process() {
    setProcessing(true);
    try {
      const result = await mutate(() => api.processPending(10));
      track("pipeline_process", {
        processed: result.processed, signal: result.signal, merged: result.merged,
        rejected: result.rejected, skipped: result.skipped, error: result.error,
      });
      notify(
        result.stopped
          ? result.stopped
          : result.busy
            ? "Разбор уже идёт в фоне. Обновим данные автоматически."
            : `Разобрано ${result.processed}: новых карточек ${result.signal}, дополнено ${result.merged}, отклонено ${result.rejected + result.skipped}`,
        result.stopped ? "error" : "success",
      );
    } catch {
      /* Error is announced by provider. */
    } finally {
      setProcessing(false);
    }
  }
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">Источники и обнаружение</span>
          <h1>Источники данных</h1>
          <p>Открытые ленты СМИ, сбор по расписанию и разбор сигналов с помощью GigaChat.</p>
        </div>
        <span className="context-tag">RSS · автоматический сбор</span>
      </div>
      <div className="stats-grid">
        <StatCard
          label="Работающих источников"
          value={`${active} / ${sources.length}`}
          note={active === sources.length ? "Все источники доступны" : "Есть источник, требующий внимания"}
          accent
        />
        <StatCard label="Документов собрано" value={totalDocuments.toLocaleString("ru")} note={`В очереди на разбор: ${stages.new ?? 0}`} />
        <StatCard
          label="Сигналов найдено"
          value={signals}
          note={totalDocuments ? `${((signals / totalDocuments) * 100).toFixed(1)}% документов` : "Пока нет документов"}
        />
        <StatCard
          label="Бюджет ИИ"
          value={usage?.configured ? `$${usage.spentUsd.toFixed(2)}` : "—"}
          note={
            (usage?.configured
              ? `из $${usage.budgetUsd.toFixed(0)} · сегодня $${usage.todayUsd.toFixed(2)} из $${usage.dailyBudgetUsd.toFixed(0)}`
              : "ИИ не настроен на сервере") +
            (pipeline?.focus?.configured ? ` · Фокус: ${pipeline.focus.used} из ${pipeline.focus.limit} компаний` : "")
          }
        />
      </div>
      <div className="analytics-grid">
        <Panel title="Контроль источников" subtitle="Ленты скачиваются напрямую, без прокси">
          <ul className="source-list">
            {sources.map((source) => (
              <li className="source-item" key={source.id}>
                <div className="source-heading">
                  <span className="source-icon">
                    <Newspaper size={22} aria-hidden="true" />
                  </span>
                  <div>
                    <h3>
                      <a href={source.url} target="_blank" rel="noreferrer noopener">
                        {source.name} <ExternalLink size={13} aria-hidden="true" />
                      </a>
                    </h3>
                    <p>
                      {source.category} · {source.schedule}
                      {source.kind === "dzen" && " · поиск через браузер, фильтр кликбейта"}
                    </p>
                  </div>
                  <SourceHealth source={source} />
                </div>
                <div className="source-details">
                  {source.hiring && source.kind !== "derived" ? (
                    <>
                      <span>
                        {source.hiring.vacancies} вакансий, актуальных {source.hiring.activeVacancies}
                      </span>
                      <span>{source.hiring.employers} работодателей</span>
                      <span>Кадровых сигналов: {source.hiring.signals}</span>
                    </>
                  ) : source.hiring ? (
                    <>
                      <span>
                        Кадровых сигналов: {source.hiring.signals}, действующих {source.hiring.activeSignals}
                      </span>
                      <span>{source.eventSignals} связано с карточками</span>
                    </>
                  ) : source.produces === "events" ? (
                    <>
                      <span>{source.events} событий</span>
                      <span>{source.eventSignals} связано с карточками</span>
                    </>
                  ) : (
                    <>
                      <span>{source.documents} документов</span>
                      <span>{source.signals} сигналов</span>
                    </>
                  )}
                  <span>Последний сбор: {source.lastRun}</span>
                  {source.errors7d > 0 && <span>Ошибок за 7 дней: {source.errors7d}</span>}
                  {source.lastError && (
                    <span className={source.status === "retry" ? "source-error" : undefined}>
                      {source.status === "retry" ? "Ошибка: " : ""}
                      {source.lastError}
                    </span>
                  )}
                  {source.produces === "events" && (
                    <button
                      className="button button-small"
                      disabled={busy || source.connection === "manual_only"}
                      onClick={() => void check(source.id, source.name)}
                    >
                      {checking === source.id ? <Spinner /> : <PlugZap size={16} aria-hidden="true" />}
                      Проверить
                    </button>
                  )}
                  <button
                    className="button button-small"
                    disabled={busy || source.status === "disabled" || source.status === "not_configured" || source.connection === "manual_only"}
                    onClick={() => void collect(source.id)}
                  >
                    {collecting === source.id ? <Spinner /> : <RotateCw size={16} aria-hidden="true" />}
                    Собрать сейчас
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel
          title="Конвейер обработки"
          subtitle="Что стало с собранными документами"
          aside={
            <button
              className="button button-small button-primary"
              disabled={busy || !usage?.configured || !(stages.new ?? 0)}
              onClick={() => void process()}
            >
              {processing ? <Spinner /> : <Play size={16} aria-hidden="true" />}
              Разобрать до 10
            </button>
          }
        >
          <BarChart
            max={Math.max(1, totalDocuments)}
            suffix=""
            rows={documentStages.map((id) => ({ label: documentStageLabels[id], value: stages[id] ?? 0 }))}
          />
          <div className="panel-note">
            <Database size={18} aria-hidden="true" />
            <p>
              Ключевые слова отсеивают нерелевантное, эмбеддинги склеивают повторы одной новости, GigaChat решает, есть ли
              инвестпроект, и готовит карточку с оценкой. Фоновый разбор идёт сам; кнопка запускает его сразу.
            </p>
          </div>
        </Panel>
      </div>
      <section className="panel documents-panel" aria-labelledby="documents-title">
        <div className="panel-heading">
          <div>
            <h2 id="documents-title">Последние документы</h2>
            <p>Каждый документ виден вместе с решением фильтра или ИИ.</p>
          </div>
          <div className="field">
            <label htmlFor="documents-stage">Этап</label>
            <select
              id="documents-stage"
              value={stage}
              onChange={(event) => setStage(event.target.value as DocumentStage | "all")}
            >
              <option value="all">Все этапы</option>
              {documentStages.map((id) => (
                <option key={id} value={id}>
                  {documentStageLabels[id]}
                </option>
              ))}
            </select>
          </div>
        </div>
        {documentsError ? (
          <p className="inline-error" role="alert">
            {documentsError}
          </p>
        ) : !documents ? (
          <div className="loading-state" role="status">
            <Spinner /> Загружаем документы…
          </div>
        ) : !documents.length ? (
          <p className="helper">Документов на этом этапе пока нет.</p>
        ) : (
          <ul className="document-list">
            {documents.map((document) => (
              <li key={document.id}>
                <div>
                  <a href={document.url} target="_blank" rel="noreferrer noopener">
                    {document.title}
                  </a>
                  <span className="table-secondary">
                    {sourceNames[document.sourceId] ?? document.sourceId}
                    {document.publishedAt && ` · ${new Date(document.publishedAt).toLocaleString("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`}
                    {document.reason && ` · ${document.reason}`}
                  </span>
                </div>
                <div className="document-status">
                  <span className={`badge ${stageBadge[document.stage]}`}>{documentStageLabels[document.stage]}</span>
                  {document.candidateId && (
                    <Link to={`/queue?case=${encodeURIComponent(document.candidateId)}`}>{document.candidateId}</Link>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
