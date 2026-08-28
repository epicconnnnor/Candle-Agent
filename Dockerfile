FROM python:3.12-slim

# Without this, print() is block-buffered and `docker compose logs`
# shows nothing at all - a failing service looks like a hung one.
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY candle_agent/ candle_agent/
# Same image for every service; compose/k8s picks the command.
CMD ["uvicorn", "candle_agent.services.api:app", "--host", "0.0.0.0", "--port", "8000"]
