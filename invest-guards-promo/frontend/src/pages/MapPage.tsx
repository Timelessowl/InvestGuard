import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  MapPinned,
  LocateFixed,
  ArrowUpRight,
  RotateCw,
  CircleAlert,
} from "lucide-react";
import { ProjectMap } from "../components/ProjectMap";
import "./map.css";
import { useApp } from "../app/AppProvider";
import {
  priorities,
  projectLocation,
  projectPriority,
  projectsOnMap,
  type Priority,
} from "../domain/geography";
import { projectCount } from "../domain/selectors";
import { Spinner } from "../components/ui";

export default function MapPage() {
  const { candidates } = useApp();
  const [params, setParams] = useSearchParams();
  const value = params.get("priority");
  const priority: Priority | "all" =
    value === "high" || value === "medium" || value === "low" ? value : "all";
  const projects = useMemo(
    () => projectsOnMap(candidates, priority),
    [candidates, priority],
  );
  const located = useMemo(() => projectsOnMap(candidates), [candidates]);
  const missing = candidates.filter((candidate) => !projectLocation(candidate));
  const [reset, setReset] = useState(0);
  const [tilesLoading, setTilesLoading] = useState(true);
  const [tileError, setTileError] = useState<string | null>(null);
  const [mapAttempt, setMapAttempt] = useState(0);
  function retryTiles() {
    setTileError(null);
    setTilesLoading(true);
    setMapAttempt((previous) => previous + 1);
  }
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">География возможностей</span>
          <h1>Карта проектов</h1>
          <p>
            Где находятся проекты и какие из них требуют внимания в первую
            очередь.
          </p>
        </div>
        <span className="context-tag">
          <MapPinned size={16} aria-hidden="true" />
          Свердловская область
        </span>
      </div>
      <section className="panel map-panel" aria-labelledby="map-heading">
        <div className="panel-heading map-heading">
          <div>
            <h2 id="map-heading">Проекты на карте</h2>
            <p role="status">
              {projectCount(projects.length)} на карте
              {missing.length ? ` · ${missing.length} без координат` : ""}
            </p>
          </div>
          <div className="map-toolbar">
            <div className="field">
              <label htmlFor="map-priority">Приоритет</label>
              <select
                id="map-priority"
                value={priority}
                onChange={(event) => {
                  const next = new URLSearchParams(params);
                  if (event.target.value === "all") next.delete("priority");
                  else next.set("priority", event.target.value);
                  setParams(next, { replace: true });
                }}
              >
                <option value="all">Все проекты</option>
                {priorities.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.label} · {item.range}
                  </option>
                ))}
              </select>
            </div>
            <button
              className="button"
              onClick={() => setReset((previous) => previous + 1)}
            >
              <LocateFixed size={17} aria-hidden="true" />
              Показать все точки
            </button>
          </div>
        </div>
        <div className="map-legend" aria-label="Цвета приоритета">
          {priorities.map((item) => (
            <div key={item.id}>
              <span
                className={`priority-dot priority-${item.id}`}
                aria-hidden="true"
              />
              <span>
                {item.label} <span className="legend-range">{item.range}</span>
              </span>
              <strong>
                {
                  located.filter(
                    (project) =>
                      projectPriority(project.candidate.score) === item.id,
                  ).length
                }
              </strong>
            </div>
          ))}
        </div>
        {tileError && (
          <div className="map-message map-error" role="alert">
            <CircleAlert size={18} aria-hidden="true" />
            <span>{tileError}</span>
            <button className="button button-small" onClick={retryTiles}>
              <RotateCw size={16} aria-hidden="true" />
              Повторить
            </button>
          </div>
        )}
        <div className="map-frame">
          <ProjectMap
            key={mapAttempt}
            projects={projects}
            reset={reset}
            onLoading={setTilesLoading}
            onError={setTileError}
          />
          {tilesLoading && !tileError && (
            <span className="map-loading" role="status">
              <Spinner />
              Загружаем карту…
            </span>
          )}
          {!projects.length && (
            <div className="map-empty" role="status">
              {priority === "all"
                ? "Нет проектов с координатами"
                : "Нет точек с выбранным приоритетом"}
            </div>
          )}
        </div>
        <div className="map-footnote">
          <span>
            Демо-точки обозначают муниципалитеты, а не точные адреса компаний.
          </span>
          <span>Перемещение: мышь или стрелки · масштаб: + / −</span>
        </div>
      </section>
      <details className="map-project-list">
        <summary>Список проектов на карте · {projects.length}</summary>
        <ul>
          {projects.map(({ candidate }) => {
            const level = projectPriority(candidate.score);
            return (
              <li key={candidate.id}>
                <span
                  className={`priority-dot priority-${level}`}
                  aria-hidden="true"
                />
                <Link to={`/queue?case=${encodeURIComponent(candidate.id)}`}>
                  <strong>{candidate.company}</strong>
                  <span>
                    {candidate.city} ·{" "}
                    {priorities.find((item) => item.id === level)!.label}{" "}
                    приоритет · {candidate.score}/100
                  </span>
                </Link>
                <ArrowUpRight size={17} aria-hidden="true" />
              </li>
            );
          })}
        </ul>
      </details>
      {missing.length > 0 && (
        <div className="map-missing">
          <h2>Без координат · {missing.length}</h2>
          <p>
            Эти карточки появятся на карте после добавления координат проекта.
          </p>
          <ul>
            {missing.map((candidate) => (
              <li key={candidate.id}>
                <Link to={`/queue?case=${encodeURIComponent(candidate.id)}`}>
                  {candidate.company} · {candidate.city}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
