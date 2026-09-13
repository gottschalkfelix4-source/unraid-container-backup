FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates unzip \
 && curl -fsSL https://rclone.org/install.sh | bash \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN mkdir -p /staging /config

EXPOSE 8080

CMD ["python", "-m", "app.main"]
