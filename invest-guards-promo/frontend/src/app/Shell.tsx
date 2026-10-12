import { useEffect, useRef, useState } from "react";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  ListFilter,
  Database,
  ChartNoAxesCombined,
  Download,
  Plus,
  CircleAlert,
  RotateCw,
  MapPinned,
} from "lucide-react";
import { useApp } from "./AppProvider";
import { queueMetrics } from "../domain/selectors";
import { AddSignalDialog } from "../components/AddSignalDialog";
import { Spinner } from "../components/ui";
import { registerWebMcp } from "../services/webMcp";

const sections = [
  {
    to: "/queue",
    label: "Очередь проверки",
    short: "Очередь",
    icon: ListFilter,
  },
  { to: "/sources", label: "Источники", short: "Источники", icon: Database },
  { to: "/map", label: "Карта проектов", short: "Карта", icon: MapPinned },
  {
    to: "/analytics",
    label: "Аналитика",
    short: "Аналитика",
    icon: ChartNoAxesCombined,
  },
];
export function Shell() {
  const { candidates, loading, loadError, busy, api, mutate, notify, refresh } =
    useApp();
  const [adding, setAdding] = useState(false);
  const [exporting, setExporting] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const main = useRef<HTMLElement>(null);
  const metrics = queueMetrics(candidates);
  useEffect(() => {
    main.current?.focus({ preventScroll: true });
    document.title = `${sections.find((section) => location.pathname.startsWith(section.to))?.label ?? "ИнвестСигнал"} — ИнвестСигнал`;
  }, [location.pathname]);
  useEffect(() => registerWebMcp(api, mutate), [api, mutate]);
  async function exportCsv() {
    setExporting(true);
    try {
      const result = await mutate(() => api.exportApproved());
      const url = URL.createObjectURL(
        new Blob([result.csv], { type: "text/csv;charset=utf-8" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "invest-signal-export.csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify(
        `Сформирован CSV: ${result.count} карточек. Статус изменён на «Экспортирован».`,
      );
    } catch {
      /* Provider preserves data and displays a recovery message. */
    } finally {
      setExporting(false);
    }
  }
  const current = sections.find((section) =>
    location.pathname.startsWith(section.to),
  );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Перейти к содержимому
      </a>
      <aside className="sidebar" aria-label="Рабочая область">
        <Link className="brand" to="/queue">
          <span className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>
            <strong>ИнвестСигнал</strong>
            <small>Аналитический контур</small>
          </span>
        </Link>
        <div className="sidebar-label">Рабочая область</div>
        <nav className="main-nav" aria-label="Основная навигация">
          {sections.map((section) => (
            <NavLink
              key={section.to}
              to={section.to}
              className={({ isActive }) =>
                `nav-link ${isActive ? "active" : ""}`
              }
            >
              <section.icon size={20} aria-hidden="true" />
              <span className="nav-label-full">{section.label}</span>
              <span className="nav-label-short">{section.short}</span>
              {section.to === "/queue" && (
                <span
                  className="nav-count"
                  aria-label={`${metrics.open} ожидают решения`}
                >
                  {metrics.open}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="demo-status">
            <span aria-hidden="true" />
            Демонстрационный режим
          </div>
          <p>Изменения сохраняются на сервере.</p>
          <div className="profile">
            <span className="profile-avatar" aria-hidden="true">
              АК
            </span>
            <div>
              <strong>Анна Крылова</strong>
              <small>Старший аналитик · демо</small>
            </div>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Рабочая область</span>
            <span aria-hidden="true">/</span>
            <strong>{current?.label ?? "ИнвестСигнал"}</strong>
          </div>
          <div className="top-actions">
            <span className="demo-badge">Демо-данные</span>
            <button
              className="button export-button"
              disabled={
                busy || loading || Boolean(loadError) || !metrics.approved
              }
              onClick={() => void exportCsv()}
              title={
                metrics.approved
                  ? "Сформировать CSV и отметить одобренные карточки как экспортированные"
                  : "Сначала одобрите проект"
              }
            >
              {exporting ? (
                <Spinner />
              ) : (
                <Download size={17} aria-hidden="true" />
              )}
              <span>{exporting ? "Экспорт…" : "Экспорт CSV"}</span>
              {metrics.approved > 0 && (
                <span className="button-count">{metrics.approved}</span>
              )}
            </button>
            <button
              className="button button-primary"
              disabled={busy || loading || Boolean(loadError)}
              onClick={() => setAdding(true)}
            >
              <Plus size={18} aria-hidden="true" />
              <span>Добавить сигнал</span>
            </button>
          </div>
        </header>
        <main
          id="main-content"
          className="main-content"
          tabIndex={-1}
          ref={main}
        >
          {loading ? (
            <div className="loading-state" role="status">
              <Spinner />
              <p>Загружаем рабочую область…</p>
              <div className="skeleton-stats" aria-hidden="true">
                <i />
                <i />
                <i />
                <i />
              </div>
            </div>
          ) : loadError ? (
            <div className="empty-state error-state" role="alert">
              <CircleAlert size={32} aria-hidden="true" />
              <h1>Данные недоступны</h1>
              <p>{loadError}</p>
              <div className="inline-actions">
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void refresh()}
                >
                  <RotateCw size={16} aria-hidden="true" />
                  Повторить
                </button>

              </div>
            </div>
          ) : (
            <Outlet />
          )}
          <footer className="workspace-footer">
            <span>ИнвестСигнал · Рабочая область аналитика</span>
            <span>Организации и источники — демонстрационные</span>
          </footer>
        </main>
      </div>
      {adding && (
        <AddSignalDialog
          onClose={() => setAdding(false)}
          onCreated={(id) => {
            setAdding(false);
            navigate(`/queue?case=${encodeURIComponent(id)}`);
          }}
        />
      )}
    </div>
  );
}
