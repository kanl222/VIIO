"""Admin-only creation of shared internal knowledge through Documents ingestion."""

import json
import re
from typing import Annotated

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from assistent.security import has_role_access


SAVE_REQUEST = re.compile(
    r'^\s*(?:(?:пожалуйста|прошу|давай|можешь|можно|нужно)\s*[,!:]?\s*)?'
    r'(?:сохран(?:и|ить|ите)|добав(?:ь|ить|ьте)|запиш(?:и|ите)|записать|save|store)\b', re.I)


def create_knowledge_write_tool(settings, hitl_enabled: bool | None = None):
    hitl = getattr(settings, 'hitl_enabled', False) if hitl_enabled is None else hitl_enabled

    @tool('save_knowledge', response_format='content_and_artifact')
    async def save_knowledge(
        title: Annotated[str, 'Краткий заголовок сохраняемого материала, до 255 символов.'],
        content: Annotated[str, 'Точный текст для общей внутренней базы знаний, до 20000 символов.'],
        config: RunnableConfig,
    ) -> tuple[str, dict | None]:
        """Save explicitly requested information to shared internal knowledge.
        Available to administrators only. All employee roles can read the saved material.
        Call only after the user asks to save/add/write information to the knowledge base.
        Saves a document, indexes it in Qdrant, and returns its id and chunk count.
        Does not publish to the external consultant or Yandex RAG.
        """
        configurable = config.get('configurable', {})
        context = configurable.get('user_context') or {}
        if not has_role_access(context, {'admin'}):
            return 'Сохранять в общую базу знаний может только администратор.', None
        if not SAVE_REQUEST.search(configurable.get('user_message', '')):
            return 'Запись не выполнена: нужна явная просьба пользователя сохранить информацию в общую базу знаний.', None
        title, content = title.strip(), content.strip()
        if not title or len(title) > 255 or not content or len(content) > 20000:
            return 'Запись не выполнена: нужен заголовок до 255 символов и текст от 1 до 20000 символов.', None
        if not settings.documents_admin_key:
            return 'Сохранение в базу знаний не настроено: отсутствует ключ записи Documents.', None

        if hitl:
            from assistent.services.human_in_the_loop import request_human_confirmation, is_confirmed
            confirmation = request_human_confirmation(
                'save_knowledge',
                {
                    'title': title,
                    'preview': content[:200] + ('...' if len(content) > 200 else ''),
                    'owner_id': context.get('user_id'),
                },
                prompt=f'Подтвердите сохранение документа «{title}» в общую базу знаний.',
            )
            if not is_confirmed(confirmation):
                return 'Запись отменена: пользователь отклонил подтверждение сохранения в базу знаний.', None

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=5), trust_env=False) as client:
                response = await client.post(settings.documents_url.rstrip('/') + '/api/v1/documents',
                    headers={'X-Internal-Token': settings.documents_admin_key},
                    json={'title': title, 'content': content, 'is_public': False,
                          'allowed_roles': ['employee', 'manager', 'programmer', 'admin'],
                          'allowed_departments': [], 'owner_id': context['user_id'],
                          'metadata': {'source': 'agent', 'created_by': context['user_id']}})
                response.raise_for_status()
                data = response.json()
                if (data.get('status') not in {'indexed', 'ready'}
                        or not isinstance(data.get('document_id'), str)
                        or not re.fullmatch(r'doc-(?:[a-f0-9]{8}|[a-f0-9]{32})', data['document_id'])
                        or type(data.get('chunks_count')) is not int or data['chunks_count'] < 1):
                    raise ValueError('Invalid indexing receipt')
        except httpx.TimeoutException:
            return 'Подтверждение записи не получено вовремя. Проверьте каталог БЗ перед повторной попыткой: документ мог сохраниться.', None
        except (httpx.HTTPError, ValueError, KeyError):
            return 'Documents не подтвердил сохранение и индексацию. Проверьте каталог БЗ; не утверждайте, что запись выполнена.', None
        receipt = {'document_id': data['document_id'], 'title': title, 'chunks_count': data['chunks_count']}
        return json.dumps({'status': 'indexed', **receipt}, ensure_ascii=False), {'saved_document': receipt}

    save_knowledge.metadata = {'allowed_roles': ['admin']}
    return save_knowledge
