FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite 資料庫會存在這裡，用 volume 持久化
VOLUME /app/data
ENV DB_PATH=/app/data/agent.db

CMD ["python", "app.py"]
