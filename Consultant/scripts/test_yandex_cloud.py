"""Opt-in live SDK check. Sends one fixed public arithmetic question only."""
import json
import logging
import os
from importlib.metadata import version
from pathlib import Path

from dotenv import dotenv_values
from yandex_ai_studio_sdk import AIStudio


def main():
    logging.disable(logging.CRITICAL)
    values = {**dotenv_values(Path(__file__).resolve().parents[2] / '.env'), **os.environ}
    key = values.get('CONSULTANT_YANDEX_API_KEY')
    folder = values.get('CONSULTANT_YANDEX_FOLDER_ID')
    name = values.get('CONSULTANT_MODEL') or values.get('CONSULTANT_YANDEX_MODEL') or 'aliceai-llm'
    if not key or not folder:
        print(json.dumps({'status': 'missing_credentials'}))
        return 2
    prompt = 'Сколько будет 2 + 2? Ответь только числом.'
    try:
        sdk = AIStudio(folder_id=folder, auth=key, enable_server_data_logging=False)
        model = sdk.models.completions(name).configure(temperature=0, max_tokens=64)
        result = model.run(prompt, timeout=30)
        answer = result[0].text
        passed = answer.strip().rstrip('.') == '4'
        print(json.dumps({'status': 'passed' if passed else 'unexpected_answer',
            'sdk': version('yandex-ai-studio-sdk'), 'prompt': prompt,
            'answer': answer}, ensure_ascii=False))
        return 0 if passed else 1
    except Exception as error:
        # SDK exception messages may include authentication metadata; do not print them.
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
