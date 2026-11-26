"""Explicit save and recall commands for private project notes."""

import httpx

from assistent.application.agent_policy import scope_key


class ProjectMemory:
    commands = {'/project-remember': 'save', '/project-recall': 'search',
                '/проект-запомни': 'save', '/проект-вспомни': 'search'}

    def __init__(self, settings):
        self.settings = settings

    async def handle(self, command, argument, context):
        operation = self.commands[command]
        project, separator, text = (part.strip() for part in argument.partition('|'))
        if not context.get('user_id'):
            return 'Для работы с памятью проектов войдите в свой аккаунт.'
        if not separator or not project or len(project) > 120 or not text or len(text) > 2000:
            return f'Формат команды: {command} название проекта | текст до 2000 символов.'
        if not self.settings.documents_admin_key:
            return 'Память проектов не настроена.'
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(
                    self.settings.documents_url.rstrip('/') + '/api/v1/project-memory/' + operation,
                    headers={'X-Internal-Token': self.settings.documents_admin_key},
                    json={'owner_scope': scope_key(context), 'project': project, 'text': text},
                )
                if response.status_code == 422:
                    return 'Запись отклонена. Уберите из текста пароли, токены и ключи.'
                response.raise_for_status()
                data = response.json()
                if operation == 'save':
                    if not data.get('id'):
                        raise ValueError('Missing save receipt')
                    return f'Сохранено в памяти проекта «{project}».'
                notes = data['notes']
                return '\n'.join(f'• {note}' for note in notes) or f'В памяти проекта «{project}» ничего не найдено.'
        except (httpx.HTTPError, ValueError, KeyError):
            return 'Память проектов временно недоступна.'
