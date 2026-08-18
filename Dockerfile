FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml ./
RUN uv sync --no-dev --no-install-project
COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", ".venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}"]
