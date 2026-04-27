FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir websockets

COPY aisstream_bridge.py .

CMD ["python", "-u", "aisstream_bridge.py"]
