FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY market_agent/ market_agent/
# Same image for every service; compose/k8s picks the command.
CMD ["uvicorn", "market_agent.services.api:app", "--host", "0.0.0.0", "--port", "8000"]
