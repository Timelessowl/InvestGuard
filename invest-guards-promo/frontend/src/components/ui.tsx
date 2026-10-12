import { useEffect, useRef, type ReactNode } from "react";
import { X, LoaderCircle, ArrowRight } from "lucide-react";
import { statusLabels, type CandidateStatus } from "../domain/models";

export function StatusBadge({ status }: { status: CandidateStatus }) {
  return (
    <span className={`badge status-${status}`}>
      <span className="status-dot" aria-hidden="true" />
      {statusLabels[status]}
    </span>
  );
}
export function Spinner() {
  return <LoaderCircle className="spinner" size={18} aria-hidden="true" />;
}
export function StatCard({
  label,
  value,
  note,
  icon,
  accent = false,
  onClick,
}: {
  label: string;
  value: ReactNode;
  note: string;
  icon?: ReactNode;
  accent?: boolean;
  onClick?: () => void;
}) {
  const content = (
    <>
      <div className="stat-top">
        <span>{label}</span>
        {icon}
      </div>
      <strong className="stat-value">{value}</strong>
      <div className="stat-note">
        {note}
        {onClick && <ArrowRight size={16} aria-hidden="true" />}
      </div>
    </>
  );
  return onClick ? (
    <button
      className={`stat-card ${accent ? "stat-accent" : ""}`}
      onClick={onClick}
    >
      {content}
    </button>
  ) : (
    <article className={`stat-card ${accent ? "stat-accent" : ""}`}>
      {content}
    </article>
  );
}
export function Panel({
  title,
  subtitle,
  children,
  aside,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}
export function Modal({
  titleId,
  onClose,
  children,
  variant = "modal",
}: {
  titleId: string;
  onClose: () => void;
  children: ReactNode;
  variant?: "modal" | "drawer";
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef(document.activeElement as HTMLElement | null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const previousOverflow = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = previousOverflow;
      queueMicrotask(() => {
        const target = opener.current?.isConnected
          ? opener.current
          : document.getElementById("main-content");
        target?.focus({ preventScroll: true });
      });
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className={`dialog dialog-${variant}`}
      aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = [
          ...event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
          ),
        ].filter((element) => element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!first) {
          event.preventDefault();
          event.currentTarget.focus();
        } else if (
          event.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === event.currentTarget)
        ) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
    >
      <div className="dialog-inner">{children}</div>
    </dialog>
  );
}
export function CloseButton({
  onClick,
  label = "Закрыть",
}: {
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      className="icon-button"
      aria-label={label}
      onClick={onClick}
    >
      <X size={20} aria-hidden="true" />
    </button>
  );
}
export function BarChart({
  rows,
  suffix = "%",
  max = 100,
}: {
  rows: { label: string; value: number }[];
  suffix?: string;
  max?: number;
}) {
  return (
    <ul className="bar-chart">
      {rows.map((row) => (
        <li key={row.label}>
          <div className="bar-label">
            <span>{row.label}</span>
            <strong>
              {row.value}
              {suffix}
            </strong>
          </div>
          <div className="bar-track" aria-hidden="true">
            <span
              style={{
                width: `${Math.max(0, Math.min(100, (row.value / Math.max(1, max)) * 100))}%`,
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
