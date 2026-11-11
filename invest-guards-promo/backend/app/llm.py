import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy import text

from .net import http_client

log = logging.getLogger(__name__)

# USD per 1M tokens; LiteLLM has no Lightning price, so it is a conservative guess.
DEFAULT_PRICES = {'GigaChat-3-Lightning': 2.0, 'GigaChat-3-Pro': 6.42, 'GigaChat-2-Max': 8.34, 'EmbeddingsGigaR': 0.18}
DAY_START = "date_trunc('day', now() AT TIME ZONE 'Asia/Yekaterinburg') AT TIME ZONE 'Asia/Yekaterinburg'"


class LlmError(Exception):
    pass


class BudgetExceeded(LlmError):
    pass


@dataclass
class LlmSettings:
    base_url: str = ''
    api_key: str = ''
    triage_model: str = 'GigaChat-3-Pro'
    smart_model: str = 'GigaChat-3-Pro'
    embed_model: str = 'EmbeddingsGigaR'
    budget_usd: float = 45.0
    daily_budget_usd: float = 3.0
    prices: dict = field(default_factory=lambda: dict(DEFAULT_PRICES))

    @classmethod
    def from_env(cls):
        env = os.environ.get
        return cls(base_url=env('LLM_BASE_URL', '').rstrip('/'), api_key=env('LLM_API_KEY', ''),
                   triage_model=env('LLM_TRIAGE_MODEL', cls.triage_model), smart_model=env('LLM_SMART_MODEL', cls.smart_model),
                   embed_model=env('LLM_EMBED_MODEL', cls.embed_model),
                   budget_usd=float(env('LLM_BUDGET_USD', cls.budget_usd)),
                   daily_budget_usd=float(env('LLM_DAILY_BUDGET_USD', cls.daily_budget_usd)),
                   prices={**DEFAULT_PRICES, **json.loads(env('LLM_PRICES_JSON', '{}'))})

    @property
    def configured(self):
        return bool(self.base_url and self.api_key)


class LlmClient:
    def __init__(self, engine, settings=None, transport=None):
        self.engine = engine
        self.settings = settings or LlmSettings.from_env()
        self.http = http_client(transport, timeout=90.0)

    def spent(self):
        with self.engine.connect() as connection:
            total, today = connection.execute(text(
                f'SELECT coalesce(sum(cost_usd), 0), coalesce(sum(cost_usd) FILTER (WHERE created_at >= {DAY_START}), 0) FROM llm_usage')).one()
        return float(total), float(today)

    def usage(self):
        with self.engine.connect() as connection:
            rows = connection.execute(text('SELECT purpose, sum(cost_usd), count(*) FROM llm_usage GROUP BY purpose')).all()
        total, today = self.spent()
        return {'spentUsd': round(total, 4), 'todayUsd': round(today, 4), 'budgetUsd': self.settings.budget_usd,
                'dailyBudgetUsd': self.settings.daily_budget_usd, 'calls': sum(r[2] for r in rows),
                'byPurpose': {r[0]: round(float(r[1]), 4) for r in rows}, 'configured': self.settings.configured}

    def ensure_available(self):
        if not self.settings.configured:
            raise LlmError('ИИ не настроен: задайте LLM_BASE_URL и LLM_API_KEY.')
        total, today = self.spent()
        if total >= self.settings.budget_usd:
            raise BudgetExceeded(f'Исчерпан общий бюджет ИИ (${self.settings.budget_usd:.0f}).')
        if today >= self.settings.daily_budget_usd:
            raise BudgetExceeded(f'Исчерпан дневной бюджет ИИ (${self.settings.daily_budget_usd:.2f}). Продолжим завтра.')

    def _post(self, path, body):
        for attempt in range(3):
            try:
                response = self.http.post(self.settings.base_url + path, json=body,
                                          headers={'Authorization': f'Bearer {self.settings.api_key}'})
            except httpx.HTTPError as error:
                if attempt == 2:
                    raise LlmError(f'Нет связи с ИИ-сервисом: {type(error).__name__}') from error
            else:
                if response.status_code < 400:
                    return response.json(), response.headers.get('x-litellm-response-cost')
                if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                    log.warning('LLM %s failed: %s %s', path, response.status_code, response.text[:300])
                    raise LlmError(f'ИИ-сервис ответил ошибкой {response.status_code}.')
            time.sleep(1.5 * (attempt + 1))

    def _record(self, model, purpose, usage, cost_header):
        prompt, completion = int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0)
        try:
            cost = float(cost_header or 0)
        except ValueError:
            cost = 0.0
        if cost <= 0:
            cost = (prompt + completion) * self.settings.prices.get(model, max(DEFAULT_PRICES.values())) / 1e6
        with self.engine.begin() as connection:
            connection.execute(text('INSERT INTO llm_usage (model, purpose, prompt_tokens, completion_tokens, cost_usd) '
                                    'VALUES (:m, :p, :pt, :ct, :c)'), {'m': model, 'p': purpose, 'pt': prompt, 'ct': completion, 'c': cost})

    def chat(self, messages, *, purpose, model=None, tools=None, schema=None, max_tokens=800, temperature=0.1):
        self.ensure_available()
        model = model or self.settings.smart_model
        body = {'model': model, 'messages': messages, 'max_tokens': max_tokens, 'temperature': temperature}
        if tools:
            body['tools'] = tools
        if schema:
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'result', 'schema': schema, 'strict': True}}
        data, cost = self._post('/chat/completions', body)
        self._record(model, purpose, data.get('usage') or {}, cost)
        try:
            return data['choices'][0]['message']
        except (KeyError, IndexError) as error:
            raise LlmError('ИИ-сервис вернул пустой ответ.') from error

    def chat_json(self, messages, schema, *, purpose, model=None, max_tokens=800):
        content = self.chat(messages, purpose=purpose, model=model, schema=schema, max_tokens=max_tokens).get('content') or ''
        try:
            return json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', content.strip()))
        except json.JSONDecodeError as error:
            raise LlmError('ИИ вернул некорректный JSON.') from error

    def embed(self, texts, *, purpose='embedding'):
        self.ensure_available()
        model = self.settings.embed_model
        data, cost = self._post('/embeddings', {'model': model, 'input': texts})
        self._record(model, purpose, data.get('usage') or {}, cost)
        return [item['embedding'] for item in sorted(data['data'], key=lambda item: item.get('index', 0))]
