import { lazy, Suspense, Component, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, Link } from "react-router-dom";
import { AppProvider } from "./AppProvider";
import { Shell } from "./Shell";
import { Spinner } from "../components/ui";

const QueuePage = lazy(() => import("../pages/QueuePage"));
const SourcesPage = lazy(() => import("../pages/SourcesPage"));
const MapPage = lazy(() => import("../pages/MapPage"));
const AnalyticsPage = lazy(() => import("../pages/AnalyticsPage"));
class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="empty-state">
        <h1>Не удалось открыть приложение</h1>
        <p>
          Обновите страницу, чтобы попробовать снова. Сохранённые данные
          останутся на сервере.
        </p>
        <button
          className="button button-primary"
          onClick={() => window.location.reload()}
        >
          Обновить страницу
        </button>
      </main>
    ) : (
      this.props.children
    );
  }
}
export function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AppProvider>
          <Suspense
            fallback={
              <div className="loading-state" role="status">
                <Spinner />
                Загружаем страницу…
              </div>
            }
          >
            <Routes>
              <Route element={<Shell />}>
                <Route index element={<Navigate to="/queue" replace />} />
                <Route path="queue" element={<QueuePage />} />
                <Route path="sources" element={<SourcesPage />} />
                <Route path="map" element={<MapPage />} />
                <Route path="analytics" element={<AnalyticsPage />} />
                <Route
                  path="*"
                  element={
                    <div className="empty-state">
                      <h1>Страница не найдена</h1>
                      <p>Вернитесь в рабочую область, чтобы продолжить.</p>
                      <Link className="button button-primary" to="/queue">
                        Открыть очередь
                      </Link>
                    </div>
                  }
                />
              </Route>
            </Routes>
          </Suspense>
        </AppProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
