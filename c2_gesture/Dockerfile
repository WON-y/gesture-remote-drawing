# 빌드 컨텍스트: 프로젝트 루트 (common/ 을 함께 복사하기 위함)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY c2_gesture/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY c2_gesture ./c2_gesture

EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/healthz')"

CMD ["uvicorn", "c2_gesture.app:app", "--host", "0.0.0.0", "--port", "8001"]
