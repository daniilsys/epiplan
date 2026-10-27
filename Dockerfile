FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY client.py epilogin.py tools.py server.py epiplan.py ./
# session.json, tokens.json and the audit log live in /data: mount a persistent volume (never bake them in)
ENV EPIPLAN_SESSION=/data/session.json EPIPLAN_TOKENS=/data/tokens.json EPIPLAN_AUDIT=/data/audit.jsonl
VOLUME /data
EXPOSE 8765
ENTRYPOINT ["python3"]
# No auth on the HTTP endpoint: bind it to a private network or put an authenticating proxy in front.
CMD ["server.py", "--http", "8765", "--host", "0.0.0.0"]
