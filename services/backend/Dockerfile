FROM python:3.11-slim AS base

WORKDIR /backend

# git: lo usan merge_service (rebase + push) y preview_service. La imagen slim no lo trae.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV BACKEND_PORT=8000
EXPOSE 8000

# Target del docker-compose.dev.yml: suma Node + yarn para correr `yarn dev`
# (los comandos viven en package.json). No forma parte de la imagen de prod.
FROM base AS dev
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g yarn@1
CMD ["yarn", "dev"]

# Target por defecto (el último): prod. Igual que `yarn start`, pero sin Node
# para que las señales lleguen directo a uvicorn.
# Un solo proceso a propósito: SQLite admite un único escritor y el session
# manager del MCP vive en memoria. Las migraciones corren antes de levantar.
FROM base AS prod
CMD alembic upgrade head && python main.py
