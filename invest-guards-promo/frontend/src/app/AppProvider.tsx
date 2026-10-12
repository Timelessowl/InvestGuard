import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { X, CircleCheck, CircleAlert } from "lucide-react";
import type { Candidate, DataSource, PilotAnalytics } from "../domain/models";
import { repository, type InvestmentRepository } from "../services/repository";

interface AppContextValue {
  candidates: Candidate[];
  sources: DataSource[];
  pilotAnalytics: PilotAnalytics | null;
  loading: boolean;
  loadError: string | null;
  busy: boolean;
  api: InvestmentRepository;
  refresh: () => Promise<void>;
  mutate: <T>(operation: () => Promise<T>) => Promise<T>;
  notify: (message: string, kind?: "success" | "error") => void;
}
const AppContext = createContext<AppContextValue | null>(null);
export function AppProvider({ children }: { children: ReactNode }) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [sources, setSources] = useState<DataSource[]>([]);
  const [pilotAnalytics, setPilotAnalytics] = useState<PilotAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{
    message: string;
    kind: "success" | "error";
  } | null>(null);
  const busyRef = useRef(false);
  const request = useRef(0);
  const refresh = useCallback(async () => {
    const ticket = ++request.current;
    try {
      const result = await repository.load();
      if (request.current !== ticket) return;
      setCandidates(result.candidates);
      setSources(result.sources);
      setPilotAnalytics(result.pilotAnalytics);
      setLoadError(null);
    } catch (error) {
      if (request.current === ticket)
        setLoadError(
          error instanceof Error
            ? error.message
            : "Не удалось загрузить данные.",
        );
    } finally {
      if (request.current === ticket) setLoading(false);
    }
  }, []);
  const notify = useCallback(
    (message: string, kind: "success" | "error" = "success") =>
      setToast({ message, kind }),
    [],
  );
  useEffect(() => {
    void refresh();
    const unsubscribe = repository.subscribe(() => {
      if (!busyRef.current) void refresh();
    });
    return () => {
      ++request.current;
      unsubscribe();
    };
  }, [refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(
      () => setToast(null),
      toast.kind === "error" ? 8000 : 5000,
    );
    return () => window.clearTimeout(timer);
  }, [toast]);
  const mutate = useCallback(
    async <T,>(operation: () => Promise<T>): Promise<T> => {
      if (busyRef.current)
        throw new Error("Дождитесь завершения текущего действия.");
      busyRef.current = true;
      setBusy(true);
      try {
        const result = await operation();
        await refresh();
        return result;
      } catch (error) {
        notify(
          error instanceof Error
            ? error.message
            : "Действие не выполнено. Повторите попытку.",
          "error",
        );
        throw error;
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [refresh, notify],
  );
  return (
    <AppContext.Provider
      value={{
        candidates,
        sources,
        pilotAnalytics,
        loading,
        loadError,
        busy,
        api: repository,
        refresh,
        mutate,
        notify,
      }}
    >
      {children}
      <div className="toast-region" aria-live="polite" aria-atomic="true">
        {toast && (
          <div className={`toast toast-${toast.kind}`}>
            {toast.kind === "success" ? (
              <CircleCheck size={20} aria-hidden="true" />
            ) : (
              <CircleAlert size={20} aria-hidden="true" />
            )}
            <span>{toast.message}</span>
            <button
              className="icon-button"
              aria-label="Закрыть уведомление"
              onClick={() => setToast(null)}
            >
              <X size={18} />
            </button>
          </div>
        )}
      </div>
    </AppContext.Provider>
  );
}
export function useApp() {
  const context = useContext(AppContext);
  if (!context) throw new Error("AppProvider missing");
  return context;
}
