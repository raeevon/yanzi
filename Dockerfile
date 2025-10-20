# База с браузерами
FROM mcr.microsoft.com/playwright/python:v1.47.2-jammy

# Безопасные локали и обновления
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Установим зависимости Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY app.py .
COPY README.md .

# Railway предоставляет $PORT — пробрасываем его в uvicorn
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# ваш общий секрет для вызовов из n8n
# (переопределите в переменных окружения Railway)
ENV SERVICE_TOKEN=change-me

# Healthcheck для Railway
HEALTHCHECK CMD curl -f http://localhost:${PORT}/health || exit 1

# Запуск API
CMD ["bash", "-lc", "python -m playwright install-deps && uvicorn app:app --host ${HOST} --port ${PORT}"]
