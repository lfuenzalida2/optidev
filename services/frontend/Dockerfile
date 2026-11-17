# `deps` también es el target del docker-compose.dev.yml (Vite con recarga).
FROM node:22-alpine AS deps

WORKDIR /frontend
COPY package.json yarn.lock ./
RUN corepack enable || true && yarn install --network-timeout 100000

FROM deps AS build
COPY . .
RUN yarn build

FROM nginx:1.27-alpine

# nginx sirve el build y hace de proxy de /api y /mcp al backend, así la app y
# el MCP quedan bajo el mismo origen. BACKEND_UPSTREAM se resuelve al arrancar.
ENV BACKEND_UPSTREAM=http://backend:8000
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /frontend/dist /usr/share/nginx/html
EXPOSE 80
