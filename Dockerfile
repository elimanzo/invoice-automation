# Reproducible environment for a system whose only real dependency is Node, and only
# to rebuild the dashboard bundle (ADR-0008) — the committed bundle means this image
# never needs Node at all. No API key needed either: with none set, the fake provider
# answers instead (ADR-0001), so `docker run` works with zero configuration.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY main.py ./
COPY data ./data

RUN pip install --no-cache-dir .

ENV INVOICE_DATA_DIR=/app/.data
EXPOSE 8000

# The dashboard, since it's the one command that needs no arguments. Override with
# e.g. `docker run <image> python main.py --invoice_path=data/invoices/invoice_1001.txt`
# for the CLI path instead.
CMD ["python", "-m", "invoice_automation.web"]
