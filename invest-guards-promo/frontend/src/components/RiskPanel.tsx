import { useState, type FormEvent } from "react";
import { RotateCw, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useApp } from "../app/AppProvider";
import { riskLabels, type Candidate } from "../domain/models";
import { Spinner } from "./ui";

const icons = { red: ShieldAlert, yellow: ShieldQuestion, green: ShieldCheck };

export function RiskBadge({ verdict }: { verdict: keyof typeof riskLabels }) {
  const Icon = icons[verdict];
  return (
    <span className={`risk-badge risk-${verdict}`}>
      <Icon size={14} aria-hidden="true" />
      {riskLabels[verdict]}
    </span>
  );
}

export function RiskPanel({ candidate }: { candidate: Candidate }) {
  const { api, mutate, busy, notify, pipeline } = useApp();
  const [inn, setInn] = useState(candidate.inn ?? "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const risk = candidate.risk;
  const focus = pipeline?.focus;
  async function run(force = false) {
    setPending(true);
    setError(null);
    try {
      const result = await mutate(() => api.riskCheck(candidate.id, inn.trim() || undefined, force));
      notify(`${result.risk?.company || candidate.company}: ${riskLabels[result.risk!.verdict].toLowerCase()}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Проверка не выполнена.");
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="detail-section risk-panel">
      <h3>
        <ShieldCheck size={18} aria-hidden="true" />
        Проверка инициатора · Контур.Фокус
      </h3>
      {error && (
        <div className="error-summary" role="alert">
          {error}
        </div>
      )}
      {risk ? (
        <>
          <div className="risk-summary">
            <RiskBadge verdict={risk.verdict} />
            <div>
              <strong>{risk.company}</strong>
              <span className="table-secondary">
                ИНН {risk.inn} · {risk.status} · проверено {new Date(risk.checkedAt).toLocaleDateString("ru")}
              </span>
            </div>
          </div>
          <ul className="risk-flags">
            {risk.flags.map((flag) => (
              <li key={flag.code + flag.title} className={`risk-flag risk-flag-${flag.level}`}>
                <span className="risk-dot" aria-hidden="true" />
                <span>
                  <strong>{flag.title}</strong>
                  {flag.detail && <span className="table-secondary">{flag.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
          {risk.ratings.length > 0 && (
            <div className="risk-ratings">
              {risk.ratings.map((rating) => (
                <span key={rating.model} className="filter-chip">
                  {rating.model}: <strong>{rating.rating}</strong>
                </span>
              ))}
            </div>
          )}
          <button className="text-button" disabled={busy} onClick={() => void run(true)}>
            {pending ? <Spinner /> : <RotateCw size={14} aria-hidden="true" />} Перепроверить
          </button>
        </>
      ) : (
        <form
          className="risk-form"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            void run();
          }}
        >
          <div className="field">
            <label htmlFor="risk-inn">ИНН компании-инициатора</label>
            <input
              id="risk-inn"
              inputMode="numeric"
              pattern="\d{10}|\d{12}"
              maxLength={12}
              value={inn}
              disabled={busy}
              placeholder="10 или 12 цифр"
              onChange={(event) => setInn(event.target.value.replace(/\D/g, ""))}
            />
          </div>
          <button className="button button-primary" type="submit" disabled={busy || !/^(\d{10}|\d{12})$/.test(inn)}>
            {pending ? <Spinner /> : <ShieldCheck size={16} aria-hidden="true" />}
            Проверить
          </button>
        </form>
      )}
      <p className="helper">
        Банкротство, суды, приставы, блокировки счетов, достоверность ЕГРЮЛ и владельцы.
        {focus?.configured ? ` Использовано ${focus.used} из ${focus.limit} проверок компаний.` : " Контур.Фокус не настроен на сервере."}
      </p>
    </section>
  );
}
