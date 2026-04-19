FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tr_bot ./tr_bot

# Default: run trading loop (override for API)
CMD ["python", "-m", "tr_bot"]
