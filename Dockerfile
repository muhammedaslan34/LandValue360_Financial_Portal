FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup --system lv360 && adduser --system --ingroup lv360 lv360
COPY requirements-runtime-lock.txt /app/
RUN pip install --no-cache-dir -r requirements-runtime-lock.txt
COPY app /app/app
COPY migrations /app/migrations
COPY scripts /app/scripts
COPY alembic.ini pyproject.toml VERSION /app/
RUN pip install --no-cache-dir --no-deps . && mkdir -p /app/data/private && chown -R lv360:lv360 /app && chmod +x /app/scripts/docker_entrypoint.sh /app/scripts/healthcheck.py
USER lv360
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --retries=5 CMD ["python", "/app/scripts/healthcheck.py"]
ENTRYPOINT ["/app/scripts/docker_entrypoint.sh"]
