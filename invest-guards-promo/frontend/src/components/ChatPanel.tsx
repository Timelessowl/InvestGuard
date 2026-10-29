import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Bot, SendHorizontal, Trash2, Wrench } from "lucide-react";
import { useApp } from "../app/AppProvider";
import type { ChatMessage, ChatReply } from "../domain/models";
import { CloseButton, Spinner } from "./ui";
import "./chat.css";

type Entry = ChatMessage & { trace?: ChatReply["trace"]; failed?: boolean };
const STORAGE_KEY = "investsignal-chat";
const suggestions = [
  "Какие новые проекты нашёл ИИ? Покажи с оценкой от 80",
  "Что сейчас с источниками и бюджетом ИИ?",
  "Собери свежие новости и разбери до 5 документов",
  "Найди проекты в Верхней Пышме",
];
const toolLabels: Record<string, string> = {
  search_candidates: "Поиск карточек",
  get_candidate: "Карточка",
  update_candidate: "Изменение карточки",
  create_signal: "Новая карточка",
  search_documents: "Поиск документов",
  pipeline_status: "Состояние конвейера",
  collect_sources: "Сбор источников",
  process_documents: "Разбор документов",
};

function loadHistory(): Entry[] {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(saved) ? saved : [];
  } catch {
    return [];
  }
}
// Plain text with **bold**, "- " lists and INV-… links; never injects HTML.
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*|INV-\d{3,})/g).map((part, index) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={index}>{part.slice(2, -2)}</strong>
    ) : /^INV-\d{3,}$/.test(part) ? (
      <Link key={index} to={`/queue?case=${part}`}>
        {part}
      </Link>
    ) : (
      <Fragment key={index}>{part}</Fragment>
    ),
  );
}
function RichText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length)
      blocks.push(
        <ul key={blocks.length}>
          {list.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </ul>,
      );
    list = [];
  };
  for (const line of text.split("\n")) {
    const item = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)/);
    if (item) list.push(item[1]);
    else {
      flush();
      if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line.replace(/^#+\s*/, ""))}</p>);
    }
  }
  flush();
  return <>{blocks}</>;
}

export function ChatPanel({ onClose }: { onClose: () => void }) {
  const { api, pipeline, refresh } = useApp();
  const [history, setHistory] = useState<Entry[]>(loadHistory);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    input.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(-40)));
    } catch {
      /* Storage may be unavailable; history stays in memory. */
    }
    end.current?.scrollIntoView({ block: "end" });
  }, [history, pending]);
  async function send(text: string) {
    const content = text.trim();
    if (!content || pending) return;
    const next: Entry[] = [...history, { role: "user", content }];
    setHistory(next);
    setDraft("");
    setPending(true);
    try {
      const reply = await api.chat(
        next.filter((entry) => !entry.failed).map(({ role, content }) => ({ role, content })),
      );
      setHistory([...next, { role: "assistant", content: reply.reply, trace: reply.trace }]);
      if (reply.changed) void refresh();
    } catch (error) {
      setHistory([
        ...next,
        {
          role: "assistant",
          failed: true,
          content: error instanceof Error ? error.message : "Ассистент недоступен. Повторите попытку.",
        },
      ]);
    } finally {
      setPending(false);
      input.current?.focus();
    }
  }
  const usage = pipeline?.usage;
  return (
    <aside className="chat-panel" aria-labelledby="chat-title">
      <header className="chat-header">
        <span className="chat-avatar" aria-hidden="true">
          <Bot size={20} />
        </span>
        <div>
          <span className="eyebrow">ИИ-ассистент · GigaChat</span>
          <h2 id="chat-title">Ассистент аналитика</h2>
          <p>
            {usage?.configured
              ? `Бюджет ИИ: $${usage.spentUsd.toFixed(2)} из $${usage.budgetUsd.toFixed(0)}`
              : "ИИ не настроен на сервере"}
          </p>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Начать новый диалог"
          title="Новый диалог"
          disabled={pending || !history.length}
          onClick={() => setHistory([])}
        >
          <Trash2 size={18} aria-hidden="true" />
        </button>
        <CloseButton onClick={onClose} label="Закрыть ассистента" />
      </header>
      <div className="chat-log" aria-live="polite">
        {!history.length && (
          <div className="chat-empty">
            <p>
              Спросите о проектах, источниках или попросите обработать новые документы. Ассистент сам вызывает
              инструменты системы и показывает, что сделал.
            </p>
            <div className="chat-suggestions">
              {suggestions.map((suggestion) => (
                <button key={suggestion} type="button" className="filter-chip" onClick={() => void send(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {history.map((entry, index) => (
          <div
            key={index}
            className={`chat-message chat-${entry.role} ${entry.failed ? "chat-failed" : ""}`}
            role={entry.failed ? "alert" : undefined}
          >
            {entry.trace && entry.trace.length > 0 && (
              <ul className="chat-trace" aria-label="Вызванные инструменты">
                {entry.trace.map((step, stepIndex) => (
                  <li key={stepIndex}>
                    <Wrench size={13} aria-hidden="true" />
                    <strong>{toolLabels[step.name] ?? step.name}</strong>
                    <span>{step.summary}</span>
                  </li>
                ))}
              </ul>
            )}
            <div className="chat-bubble">
              {entry.role === "assistant" ? <RichText text={entry.content} /> : entry.content}
            </div>
          </div>
        ))}
        {pending && (
          <div className="chat-message chat-assistant" role="status">
            <div className="chat-bubble chat-thinking">
              <Spinner /> Ассистент работает с данными…
            </div>
          </div>
        )}
        <div ref={end} />
      </div>
      <form
        className="chat-form"
        onSubmit={(event: FormEvent) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <label htmlFor="chat-input" className="sr-only">
          Сообщение ассистенту
        </label>
        <textarea
          id="chat-input"
          ref={input}
          rows={2}
          maxLength={4000}
          value={draft}
          disabled={pending}
          placeholder="Например: какие проекты требуют реакции на этой неделе?"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send(draft);
            }
          }}
        />
        <button className="button button-primary" type="submit" disabled={pending || !draft.trim()} aria-label="Отправить">
          {pending ? <Spinner /> : <SendHorizontal size={18} aria-hidden="true" />}
        </button>
      </form>
    </aside>
  );
}
