# Замена моделей агентов

В `.env` можно независимо настроить модели:

| Агент | Провайдер | Модель |
| --- | --- | --- |
| Основной | CHAT_PROVIDER | CHAT_MODEL |
| Программист | CODING_PROVIDER | CODING_MODEL |
| PostgreSQL | DATABASE_PROVIDER | DATABASE_MODEL |
| Публичный консультант | CONSULTANT_PROVIDER | CONSULTANT_MODEL |

Провайдеры внутренних агентов: `lmstudio`, `ollama`.
Если провайдер не указан, используется LLM_PROVIDER. Если имя модели пустое,
используется LMSTUDIO_MODEL или OLLAMA_MODEL выбранного провайдера.
Настройки температуры и лимитов программиста сохраняются отдельно.

Пример:

```dotenv
CHAT_PROVIDER=lmstudio
CHAT_MODEL=qwen/qwen3-4b-2507
CODING_PROVIDER=ollama
CODING_MODEL=qwen2.5-coder:7b
DATABASE_PROVIDER=lmstudio
DATABASE_MODEL=qwen/qwen3-4b-2507
```

Указанные модели должны быть доступны на соответствующем сервере.
После изменения пересоберите и перезапустите `assistent` (для публичного консультанта — `consultant`).
Переключения в веб-интерфейсе нет.

Внутренние агенты получают историю, документы, память и результаты БД.
Yandex Cloud для них запрещён: подтверждение публичности отдельного вопроса
не разрешает отправку этого дополнительного контекста. Для облака доступен
изолированный endpoint, описанный в [yandex-cloud.md](yandex-cloud.md).
