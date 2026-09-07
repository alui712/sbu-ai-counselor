FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py server.py ./
COPY tools ./tools
COPY web ./web
COPY sbu_courses.json sbu_programs.json ./

# Optional RAG package (imported only if used)
COPY rag ./rag

EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
