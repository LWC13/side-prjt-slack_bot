FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME /app/data
ENV DB_PATH=/app/data/agent.db

CMD ["python", "app.py"]