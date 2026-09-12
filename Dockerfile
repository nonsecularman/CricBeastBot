FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite data (only used if DATABASE_URL points at sqlite) lives here so it
# can be mounted as a volume and survive container recreation.
VOLUME ["/app/data"]

CMD ["python", "main.py"]
