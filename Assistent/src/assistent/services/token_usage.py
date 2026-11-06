"""Process-lifetime counters of provider-reported model usage; no prompt storage."""

from datetime import datetime, timezone
from threading import Lock

from langchain_core.callbacks import BaseCallbackHandler


class TokenUsageCounter(BaseCallbackHandler):
    def __init__(self):
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._lock = Lock()
        self._totals = dict(input_tokens=0, output_tokens=0, total_tokens=0,
                            reported_calls=0, unreported_calls=0)

    def on_llm_end(self, response, **kwargs):
        usages = [generation.message.usage_metadata
                  for group in response.generations for generation in group
                  if getattr(getattr(generation, 'message', None), 'usage_metadata', None)]
        if not usages:
            raw = (response.llm_output or {}).get('token_usage')
            if raw and 'prompt_tokens' in raw and 'completion_tokens' in raw:
                usages = [dict(input_tokens=raw['prompt_tokens'], output_tokens=raw['completion_tokens'],
                               total_tokens=raw.get('total_tokens', raw['prompt_tokens'] + raw['completion_tokens']))]
        with self._lock:
            if not usages:
                self._totals['unreported_calls'] += 1
                return
            self._totals['reported_calls'] += 1
            for usage in usages:
                for key in ('input_tokens', 'output_tokens', 'total_tokens'):
                    self._totals[key] += usage[key]

    def snapshot(self):
        with self._lock:
            return dict(self._totals, started_at=self.started_at)


token_usage = TokenUsageCounter()
