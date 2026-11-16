"""Run inside either agent container; sends one fixed public question to Yandex RAG."""
import argparse
import json
import httpx

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=['assistant', 'consultant'], required=True)
    args = parser.parse_args()
    if args.agent == 'assistant':
        from assistent.config import Settings
        settings = Settings()
        url = 'http://127.0.0.1:8000/api/v1/chat'
        headers = {'X-Internal-Token': settings.chat_api_key}
    else:
        from viio_consultant.config import ConsultantSettings
        settings = ConsultantSettings()
        url = 'http://127.0.0.1:8000/api/v1/consultant/chat'
        headers = {'X-Consultant-Key': settings.api_key}
    payload = {'message': 'Как найти электронный рецепт по СНИЛС?', 'rag_backend': 'yandex'}
    with httpx.Client(timeout=90, trust_env=False) as client:
        blocked = client.post(url, headers=headers, json=payload)
        assert blocked.status_code == 403, blocked.status_code
        payload['confirmed_public_for_external_cloud'] = True
        response = client.post(url, headers=headers, json=payload)
        result = response.json()
        assert response.status_code == 200, (response.status_code, result)
        assert result['reply'] and result['sources'], result
        assert any('СНИЛС' in source['title'] for source in result['sources']), result
        print(json.dumps({'agent': args.agent, 'status': 'passed', 'confirmation_block': blocked.status_code,
            'reply': result['reply'], 'sources': result['sources']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
