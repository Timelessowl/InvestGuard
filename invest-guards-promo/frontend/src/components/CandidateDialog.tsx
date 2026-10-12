import { useEffect, useRef, useState } from "react";
import {
  Check,
  Eye,
  X,
  FileCheck2,
  ShieldCheck,
  CircleAlert,
} from "lucide-react";
import {
  owners,
  nextActions,
  type Candidate,
  type Decision,
} from "../domain/models";
import { useApp } from "../app/AppProvider";
import { BarChart, CloseButton, Modal, Spinner, StatusBadge } from "./ui";

function Excerpt({ text, highlights }: { text: string; highlights: string[] }) {
  // Render untrusted excerpts as React text; mark only known text segments, never HTML.
  const segments: { text: string; mark: boolean }[] = [{ text, mark: false }];
  for (const highlight of highlights.filter(Boolean)) {
    for (let index = segments.length - 1; index >= 0; index--) {
      const segment = segments[index];
      if (segment.mark || !segment.text.includes(highlight)) continue;
      const parts = segment.text.split(highlight);
      segments.splice(
        index,
        1,
        ...parts.flatMap((part, partIndex) =>
          partIndex === parts.length - 1
            ? [{ text: part, mark: false }]
            : [
                { text: part, mark: false },
                { text: highlight, mark: true },
              ],
        ),
      );
    }
  }
  return (
    <>
      {segments.map((segment, index) =>
        segment.mark ? <mark key={index}>{segment.text}</mark> : segment.text,
      )}
    </>
  );
}
export function CandidateDialog({
  candidate,
  onClose,
}: {
  candidate: Candidate;
  onClose: () => void;
}) {
  const { api, mutate, busy, notify } = useApp();
  const [rejectConfirm, setRejectConfirm] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const confirmButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (rejectConfirm) confirmButton.current?.focus();
  }, [rejectConfirm]);
  const close = () => {
    if (!busy) onClose();
  };
  async function updateAssignment(
    field: "owner" | "nextAction",
    value: string,
  ) {
    setPending(field);
    setSaveError(null);
    try {
      await mutate(() => api.update(candidate.id, { [field]: value }));
      notify(
        field === "owner"
          ? "Ответственный сохранён"
          : "Следующее действие сохранено",
      );
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : "Изменения не сохранены. Повторите попытку.",
      );
    } finally {
      setPending(null);
    }
  }
  async function decide(status: Decision) {
    setPending(status);
    setSaveError(null);
    try {
      await mutate(() => api.update(candidate.id, { status }));
      notify(
        status === "approved"
          ? `${candidate.company}: проект одобрен и готов к экспорту`
          : status === "rejected"
            ? "Сигнал отклонён и сохранён для оценки качества"
            : "Карточка оставлена на проверке",
      );
      onClose();
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : "Решение не сохранено. Повторите попытку.",
      );
    } finally {
      setPending(null);
    }
  }
  return (
    <Modal titleId="candidate-title" onClose={close} variant="drawer">
      <header className="dialog-header">
        <div>
          <span className="eyebrow">
            {candidate.id} · {candidate.signalType}
          </span>
          <h2 id="candidate-title">{candidate.company}</h2>
          <p>
            {candidate.sector} · {candidate.city}
          </p>
          <StatusBadge status={candidate.status} />
        </div>
        <CloseButton onClick={close} label="Закрыть карточку" />
      </header>
      <div className="dialog-body candidate-body">
        {saveError && (
          <div className="error-summary" role="alert">
            {saveError}
          </div>
        )}
        <p className="candidate-summary">{candidate.summary}</p>
        <dl className="detail-metrics">
          <div>
            <dt>Инвестиции</dt>
            <dd>{candidate.amount}</dd>
          </div>
          <div>
            <dt>Стадия</dt>
            <dd>{candidate.stage}</dd>
          </div>
          <div>
            <dt>Приоритет</dt>
            <dd>
              {candidate.score}
              <small> / 100</small>
            </dd>
          </div>
        </dl>
        <section className="detail-section">
          <h3>
            <ShieldCheck size={18} aria-hidden="true" />
            Почему проект в очереди
          </h3>
          <BarChart
            rows={Object.entries(candidate.factors).map(([label, value]) => ({
              label,
              value,
            }))}
            suffix=""
          />
          <p className="helper">
            Предварительная оценка по демо-модели. Решение принимает эксперт.
          </p>
        </section>
        <section className="detail-section">
          <h3>
            <FileCheck2 size={18} aria-hidden="true" />
            Доказательства{" "}
            <span className="count-pill">{candidate.evidence.length}</span>
          </h3>
          <div className="evidence-list">
            {candidate.evidence.map((evidence, index) => (
              <article className="evidence" key={index}>
                <div className="evidence-meta">
                  <strong>{evidence.source}</strong>
                  <span>{evidence.date}</span>
                </div>
                <p>
                  <Excerpt
                    text={evidence.excerpt}
                    highlights={evidence.highlights}
                  />
                </p>
              </article>
            ))}
          </div>
        </section>
        <section className="detail-section">
          <h3>Следующий шаг</h3>
          <div className="recommendation">
            <span className="eyebrow">Рекомендация</span>
            <strong>{candidate.recommendation}</strong>
            <p>{candidate.rationale}</p>
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="candidate-owner">
                Ответственный {pending === "owner" && <Spinner />}
              </label>
              <select
                id="candidate-owner"
                value={candidate.owner}
                disabled={busy}
                onChange={(event) =>
                  void updateAssignment("owner", event.target.value)
                }
              >
                {[...new Set([...owners, candidate.owner])].map((owner) => (
                  <option key={owner}>{owner}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="candidate-action">
                Следующее действие {pending === "nextAction" && <Spinner />}
              </label>
              <select
                id="candidate-action"
                value={candidate.nextAction}
                disabled={busy}
                onChange={(event) =>
                  void updateAssignment("nextAction", event.target.value)
                }
              >
                {nextActions.map((action) => (
                  <option key={action}>{action}</option>
                ))}
              </select>
            </div>
          </div>
          <p className="helper" role="status">
            {busy
              ? "Сохраняем изменения…"
              : "Ответственный и действие сохраняются автоматически."}
          </p>
        </section>
        {rejectConfirm && (
          <div className="rejection-confirm" role="alert">
            <CircleAlert size={20} aria-hidden="true" />
            <div>
              <strong>Отклонить этот сигнал?</strong>
              <p>Карточка останется в архиве отклонённых.</p>
              <div className="inline-actions">
                <button
                  ref={confirmButton}
                  className="button button-danger"
                  disabled={busy}
                  onClick={() => void decide("rejected")}
                >
                  {pending === "rejected" && <Spinner />}Подтвердить отклонение
                </button>
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => setRejectConfirm(false)}
                >
                  Отмена
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
      <footer className="dialog-footer decision-footer">
        <button
          className="button button-danger"
          disabled={busy || candidate.status === "rejected"}
          onClick={() => setRejectConfirm(true)}
        >
          <X size={17} aria-hidden="true" />
          Отклонить
        </button>
        <button
          className="button"
          disabled={busy || candidate.status === "review"}
          onClick={() => void decide("review")}
        >
          {pending === "review" ? (
            <Spinner />
          ) : (
            <Eye size={17} aria-hidden="true" />
          )}
          Наблюдать
        </button>
        <button
          className="button button-primary"
          disabled={
            busy ||
            candidate.status === "approved" ||
            candidate.status === "exported"
          }
          onClick={() => void decide("approved")}
        >
          {pending === "approved" ? (
            <Spinner />
          ) : (
            <Check size={18} aria-hidden="true" />
          )}
          Одобрить проект
        </button>
      </footer>
    </Modal>
  );
}
