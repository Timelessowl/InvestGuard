import { useRef, useState, type FormEvent } from "react";
import { Plus, FileText } from "lucide-react";
import {
  newSignalSchema,
  sectorOptions,
  type NewSignal,
} from "../domain/models";
import { useApp } from "../app/AppProvider";
import { CloseButton, Modal, Spinner } from "./ui";

export function AddSignalDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const { api, mutate, busy, notify } = useApp();
  const [values, setValues] = useState<NewSignal>({
    company: "",
    sector: "",
    city: "",
    signal: "",
    source: "",
  });
  const [errors, setErrors] = useState<
    Partial<Record<keyof NewSignal, string>>
  >({});
  const [submitted, setSubmitted] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const summary = useRef<HTMLDivElement>(null);
  const dirty = Object.values(values).some(Boolean);
  const close = () => {
    if (
      !busy &&
      (!dirty ||
        window.confirm("Закрыть форму? Введённые данные не будут сохранены."))
    )
      onClose();
  };
  function validate(key: keyof NewSignal) {
    const result = newSignalSchema.shape[key].safeParse(values[key]);
    setErrors((previous) => ({
      ...previous,
      [key]: result.success ? undefined : result.error.issues[0]?.message,
    }));
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaveError(null);
    const result = newSignalSchema.safeParse(values);
    setSubmitted(true);
    if (!result.success) {
      setErrors(
        Object.fromEntries(
          result.error.issues.map((issue) => [issue.path[0], issue.message]),
        ),
      );
      requestAnimationFrame(() => summary.current?.focus());
      return;
    }
    try {
      const candidate = await mutate(() => api.create(result.data));
      notify(`Карточка ${candidate.id} создана`);
      onCreated(candidate.id);
    } catch (error) {
      setSaveError(
        error instanceof Error
          ? error.message
          : "Не удалось сохранить карточку. Повторите попытку.",
      );
    }
  }
  const fieldProps = (name: keyof NewSignal) => ({
    id: `signal-${name}`,
    name,
    value: values[name],
    disabled: busy,
    "aria-invalid": Boolean(errors[name]),
    "aria-describedby": errors[name] ? `error-${name}` : undefined,
    onChange: (
      event: React.ChangeEvent<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >,
    ) => setValues((previous) => ({ ...previous, [name]: event.target.value })),
    onBlur: () => validate(name),
  });
  const error = (name: keyof NewSignal) =>
    errors[name] && (
      <p className="field-error" id={`error-${name}`}>
        {errors[name]}
      </p>
    );
  return (
    <Modal titleId="add-title" onClose={close}>
      <header className="dialog-header">
        <div>
          <span className="eyebrow">
            <FileText size={16} aria-hidden="true" /> Ручной ввод
          </span>
          <h2 id="add-title">Добавить сигнал</h2>
          <p>Соберите первичные факты — оценку уточнит эксперт.</p>
        </div>
        <CloseButton onClick={close} />
      </header>
      <form onSubmit={submit} noValidate>
        <div className="dialog-body form-grid">
          {saveError && (
            <div className="error-summary" role="alert">
              {saveError}
            </div>
          )}
          {submitted && Object.values(errors).some(Boolean) && (
            <div
              className="error-summary"
              ref={summary}
              tabIndex={-1}
              role="alert"
            >
              <strong>Проверьте поля формы</strong>
              <ul>
                {Object.entries(errors)
                  .filter(([, message]) => message)
                  .map(([key, message]) => (
                    <li key={key}>
                      <a
                        href={`#signal-${key}`}
                        onClick={(event) => {
                          event.preventDefault();
                          document.getElementById(`signal-${key}`)?.focus();
                        }}
                      >
                        {message}
                      </a>
                    </li>
                  ))}
              </ul>
            </div>
          )}
          <div className="field">
            <label htmlFor="signal-company">
              Компания <span>(обязательно)</span>
            </label>
            <input
              {...fieldProps("company")}
              autoFocus
              autoComplete="organization"
              maxLength={160}
              placeholder="Например, ООО «УралПром»"
            />
            {error("company")}
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="signal-sector">
                Отрасль <span>(обязательно)</span>
              </label>
              <select {...fieldProps("sector")}>
                <option value="">Выберите отрасль</option>
                {sectorOptions.map((sector) => (
                  <option key={sector}>{sector}</option>
                ))}
              </select>
              {error("sector")}
            </div>
            <div className="field">
              <label htmlFor="signal-city">
                Муниципалитет <span>(обязательно)</span>
              </label>
              <input
                {...fieldProps("city")}
                autoComplete="address-level2"
                maxLength={120}
                placeholder="Екатеринбург"
              />
              {error("city")}
            </div>
          </div>
          <div className="field">
            <label htmlFor="signal-signal">
              Описание сигнала <span>(обязательно)</span>
            </label>
            <textarea
              {...fieldProps("signal")}
              rows={4}
              maxLength={4000}
              placeholder="Что указывает на инвестиционный проект?"
            />
            {error("signal")}
            <small>Укажите событие, масштаб и предполагаемые сроки.</small>
          </div>
          <div className="field">
            <label htmlFor="signal-source">
              Источник <span>(обязательно)</span>
            </label>
            <input
              {...fieldProps("source")}
              maxLength={1000}
              placeholder="Название документа или ссылка"
            />
            {error("source")}
            <small>Источник будет сохранён в доказательствах карточки.</small>
          </div>
        </div>
        <footer className="dialog-footer">
          <button
            type="button"
            className="button"
            onClick={close}
            disabled={busy}
          >
            Отмена
          </button>
          <button
            className="button button-primary"
            type="submit"
            disabled={busy}
          >
            {busy ? <Spinner /> : <Plus size={18} aria-hidden="true" />}
            {busy ? "Создание…" : "Создать карточку"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
