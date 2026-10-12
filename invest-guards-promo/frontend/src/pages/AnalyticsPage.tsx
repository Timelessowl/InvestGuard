import { ArrowUpRight, Timer, TrendingUp, CircleCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { useApp } from "../app/AppProvider";
import { queueMetrics } from "../domain/selectors";
import { Panel, BarChart, StatCard } from "../components/ui";

export default function AnalyticsPage() {
  const { candidates, pilotAnalytics } = useApp();
  if (!pilotAnalytics) return null;
  const metrics = queueMetrics(candidates);
  const resolved = metrics.approved + metrics.exported + metrics.rejected;
  const confirmed = metrics.approved + metrics.exported;
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">Качество и результат</span>
          <h1>Аналитика контура</h1>
          <p>
            Текущая очередь и исторические показатели демонстрационного пилота.
          </p>
        </div>
        <span className="context-tag">Демо-пилот · сентябрь 2026</span>
      </div>
      <div className="section-label">
        <span>Показатели пилота</span>
        <span className="small-label">Пример · фиксированные значения</span>
      </div>
      <div className="stats-grid three-stats">
        <StatCard
          label="Подтверждение сигналов"
          value={pilotAnalytics.confirmationRate}
          note="Подтверждено экспертами в примере"
          icon={<CircleCheck size={21} aria-hidden="true" />}
          accent
        />
        <StatCard
          label="Время до решения"
          value={pilotAnalytics.medianReview}
          note="Медиана · цель менее 12 часов"
          icon={<Timer size={21} aria-hidden="true" />}
        />
        <StatCard
          label="Преимущество во времени"
          value={pilotAnalytics.leadTime}
          note="Среднее опережение в примере"
          icon={<TrendingUp size={21} aria-hidden="true" />}
        />
      </div>
      <div className="analytics-grid">
        <Panel
          title="Подтверждение по типам"
          subtitle="Исторический пример, доля одобренных сигналов"
        >
          <BarChart rows={pilotAnalytics.signals} />
          <p className="chart-caption">
            Расширение производства чаще всего подтверждается экспертами в этом
            примере.
          </p>
        </Panel>
        <Panel
          title="Воронка текущей очереди"
          subtitle="Обновляется после ваших решений"
          aside={
            <Link
              className="icon-link"
              to="/queue"
              aria-label="Перейти к очереди"
            >
              <ArrowUpRight size={20} aria-hidden="true" />
            </Link>
          }
        >
          <BarChart
            max={candidates.length}
            suffix=""
            rows={[
              { label: "Всего обнаружено", value: candidates.length },
              { label: "Ожидают решения", value: metrics.open },
              { label: "Подтверждено всего", value: confirmed },
              { label: "Из них экспортировано", value: metrics.exported },
              { label: "Отклонено", value: metrics.rejected },
            ]}
          />
          <div className="analytics-summary">
            <strong>
              {resolved ? Math.round((confirmed / resolved) * 100) : 0}%
            </strong>
            <p>
              {resolved
                ? `подтверждено среди ${resolved} карточек с решением`
                : "Пока нет карточек с решением"}
            </p>
          </div>
        </Panel>
      </div>
    </>
  );
}
