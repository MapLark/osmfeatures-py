FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[mcp]"

EXPOSE 8081

CMD ["osmfeatures", "mcp", "--http", "--host", "0.0.0.0", "--port", "8081"]
