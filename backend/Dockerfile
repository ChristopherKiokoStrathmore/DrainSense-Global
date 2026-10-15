# Ngao backend image (OCI Always Free VM, or any Docker host).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
ENV STATIC_ROOT=/app/staticfiles
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput -v0
RUN useradd --create-home ngao && chown -R ngao /app
USER ngao

EXPOSE 8000
# Two sync workers + threads: USSD calls stay fast while a send runs in the background.
CMD ["gunicorn", "ngao_core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "30", "--access-logfile", "-"]
