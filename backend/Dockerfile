FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends git gh nodejs npm ca-certificates \
    && npm install -g @github/copilot \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config ./config

EXPOSE 8015

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8015"]