# Ngao USSD callback (Wasiliana). Python 3.12: oracledb 2.4.1 ships wheels up to 3.13.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home ngao && chown -R ngao /app
USER ngao

EXPOSE 8080
# One worker: the per-session cache (store.SessionCache) is in memory. Threads keep hops concurrent.
CMD ["gunicorn", "callback:app", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "15", "--access-logfile", "-"]
