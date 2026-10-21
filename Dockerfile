FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 app && mkdir /data && chown app /data

USER app
ENV BBGPROXY_DATABASE_URL=sqlite:////data/bloombergproxy.db \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000

# Mit BBGPROXY_DEMO_SEED=1 werden beim Start Beispiel-Feldlisten und Platzhalterpreise angelegt
# (nur in eine leere Datenbank). Im Betrieb weglassen.
CMD ["sh", "-c", "if [ \"$BBGPROXY_DEMO_SEED\" = \"1\" ]; then python -m bloombergproxy.devseed; fi; exec uvicorn bloombergproxy.main:create_app --factory --host 0.0.0.0 --port 8000"]
