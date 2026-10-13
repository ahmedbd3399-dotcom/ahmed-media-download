FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg لازم لتحويل MP3 والعلامة المائية وأدوات الفيديو
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY downloading_bot.py .

# المنفذ بتاع سيرفر الـ keep-alive (بعض الاستضافات بتحدده تلقائياً في PORT)
EXPOSE 8080

CMD ["python", "downloading_bot.py"]
