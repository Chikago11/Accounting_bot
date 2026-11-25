FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --no-create-home app

COPY *.py category_aliases.json ./
COPY voice ./voice
COPY scripts ./scripts
USER 1000:1000
CMD ["python", "main.py"]
