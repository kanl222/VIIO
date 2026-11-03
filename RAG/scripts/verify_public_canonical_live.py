"""Read-only end-to-end checks of the installed public corpus and cloud consultant."""
import hashlib
import json
from pathlib import Path
import httpx
from viio_documents.config import Settings


def main():
    s = Settings()
    report = json.loads(Path('/tmp/public-canonical-import.json').read_text())
    checks = []
    publication_ids = {p['id'] for row in report['results'] for p in row['publications']}
    with httpx.Client(timeout=90, trust_env=False) as client:
        for row in report['results']:
            response = client.get('http://127.0.0.1:8000/api/v1/documents/download/' + row['document_id'],
                params={'user_role': 'guest'}, headers={'X-Internal-Token': s.documents_read_key})
            assert response.status_code == 200, (row['file'], response.status_code)
            assert hashlib.sha256(response.content).hexdigest() == row['sha256'], row['file']
        checks.append({'check': 'guest_download_originals_exact_hash', 'passed': len(report['results'])})
        query = 'Как создать заявку на предоставление доступа сотруднику в ИС ЛО?'
        search = client.post('http://rag:8000/api/v1/knowledge/search',
            headers={'X-Internal-Token': s.documents_read_key},
            json={'query': query, 'user_context': {'role': 'guest', 'departments': []}})
        assert search.status_code == 200 and search.json().get('context'), search.status_code
        checks.append({'check': 'guest_knowledge_search', 'status': search.status_code})
        url = 'http://consultant:8000/api/v1/consultant/chat'
        headers = {'X-Consultant-Key': s.consultant_api_key}
        assert client.post(url, json={'message': query, 'confirmed_public_for_external_cloud': True}).status_code == 401
        assert client.post(url, headers=headers, json={'message': query}).status_code == 403
        checks.append({'check': 'authentication_and_public_confirmation', 'passed': True})
        for question, expected_file in [(query, '01.txt'), ('Как найти электронный рецепт по СНИЛС?', '29.txt'),
                         ('Для чего нужен модуль Управление сотрудниками в ИС ЛО?', '01.txt')]:
            response = client.post(url, headers=headers,
                json={'message': question, 'confirmed_public_for_external_cloud': True})
            assert response.status_code == 200, response.status_code
            result = response.json()
            assert result['reply'] and result['sources'], result
            assert 'не наш' not in result['reply'].lower() and 'нет сведений' not in result['reply'].lower(), result
            assert all(source['publication_id'] in publication_ids for source in result['sources'])
            expected_ids = {p['id'] for row in report['results'] if row['file'] == expected_file
                for p in row['publications']}
            assert expected_ids.intersection(source['publication_id'] for source in result['sources']), result
            checks.append({'check': 'cloud_consultant', 'question': question,
                'reply': result['reply'], 'sources': result['sources']})
            print(json.dumps({'stage': 'cloud_answer_ok', 'sources': len(result['sources'])}), flush=True)
    target = Path('/tmp/public-canonical-live-results.json')
    target.write_text(json.dumps({'status': 'passed', 'checks': checks}, ensure_ascii=False, indent=2))
    print(json.dumps({'status': 'passed', 'guest_downloads': 41, 'cloud_questions': 3}), flush=True)


if __name__ == '__main__':
    main()
