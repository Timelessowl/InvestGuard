import {
  Database,
  Newspaper,
  BriefcaseBusiness,
  Factory,
  RotateCw,
  CircleCheck,
  TriangleAlert,
} from "lucide-react";
import { useApp } from "../app/AppProvider";
import { Panel, Spinner, StatCard, BarChart } from "../components/ui";

const sourceIcons = {
  procurement: Database,
  gisp: Factory,
  news: Newspaper,
  jobs: BriefcaseBusiness,
};
export default function SourcesPage() {
  const { sources, pilotAnalytics, api, mutate, busy, notify } = useApp();
  const active = sources.filter((source) => source.status === "active").length;
  async function retry(id: string) {
    try {
      await mutate(() => api.retrySource(id));
      notify("Тестовый сбор завершён. Статус источника обновлён.");
    } catch {
      /* Error is announced by provider. */
    }
  }
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">Источники и обнаружение</span>
          <h1>Источники данных</h1>
          <p>
            Состояние коннекторов, расписание сбора и распределение сигналов.
          </p>
        </div>
        <span className="context-tag">Демонстрационный сбор</span>
      </div>
      <div className="stats-grid">
        <StatCard
          label="Работающих источников"
          value={`${active} / ${sources.length}`}
          note={
            active === sources.length
              ? "Все источники доступны"
              : "Есть источник, требующий внимания"
          }
          accent
        />
        <StatCard
          label="Документов в примере"
          value={sources
            .reduce((sum, source) => sum + source.documents, 0)
            .toLocaleString("ru")}
          note="Последний демонстрационный цикл"
        />
        <StatCard
          label="Сигналов в примере"
          value={pilotAnalytics?.sourceSignals ?? 0}
          note="2,8% обработанных документов"
        />
        <StatCard
          label="Ожидают повтора"
          value={sources.length - active}
          note="Можно запустить тестовый сбор"
        />
      </div>
      <div className="analytics-grid">
        <Panel
          title="Контроль источников"
          subtitle="Коннекторы работают в демонстрационном режиме"
        >
          <ul className="source-list">
            {sources.map((source) => {
              const Icon =
                sourceIcons[source.id as keyof typeof sourceIcons] ?? Database;
              return (
                <li className="source-item" key={source.id}>
                  <div className="source-heading">
                    <span className="source-icon">
                      <Icon size={22} aria-hidden="true" />
                    </span>
                    <div>
                      <h3>{source.name}</h3>
                      <p>{source.schedule}</p>
                    </div>
                    <span
                      className={`source-health ${source.status === "active" ? "health-active" : "health-warning"}`}
                    >
                      {source.status === "active" ? (
                        <CircleCheck size={15} aria-hidden="true" />
                      ) : (
                        <TriangleAlert size={15} aria-hidden="true" />
                      )}
                      {source.status === "active" ? "Работает" : "Нужен повтор"}
                    </span>
                  </div>
                  <div className="source-details">
                    <span>{source.documents} документов</span>
                    <span>Последний сбор: {source.lastRun}</span>
                    {source.status === "retry" && (
                      <button
                        className="button button-small"
                        disabled={busy}
                        onClick={() => void retry(source.id)}
                      >
                        {busy ? (
                          <Spinner />
                        ) : (
                          <RotateCw size={16} aria-hidden="true" />
                        )}
                        Повторить сбор
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </Panel>
        <Panel
          title="Сигналы по источникам"
          subtitle="Распределение в примере за 7 дней"
        >
          <BarChart
            rows={[...sources]
              .sort((a, b) => b.share - a.share)
              .map((source) => ({
                label: source.category,
                value: source.share,
              }))}
          />
          <div className="panel-note">
            <Database size={18} aria-hidden="true" />
            <p>
              Источники работают в демонстрационном режиме. Тестовый повтор
              сохраняет статус на сервере; документы не собираются.
            </p>
          </div>
        </Panel>
      </div>
    </>
  );
}
