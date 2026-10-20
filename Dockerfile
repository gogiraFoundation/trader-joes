FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV ARTIFACTS_DIR=/app/artifacts

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tr_bot ./tr_bot
COPY scripts ./scripts

RUN chmod +x /app/scripts/supervise.sh \
    && useradd --create-home --uid 1000 --shell /bin/bash app \
    && mkdir -p /app/artifacts \
    && chown -R app:app /app

USER app

ENTRYPOINT ["scripts/supervise.sh"]
CMD ["bot"]
