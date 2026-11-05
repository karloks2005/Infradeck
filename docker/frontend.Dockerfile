FROM node:20-alpine AS build

WORKDIR /app

COPY src/frontend/package*.json ./
RUN npm ci

COPY src/frontend/ ./
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ARG VITE_LIVE_GRAPH_REFRESH_INTERVAL_SECONDS=3
ENV VITE_LIVE_GRAPH_REFRESH_INTERVAL_SECONDS=$VITE_LIVE_GRAPH_REFRESH_INTERVAL_SECONDS
RUN npm run build

FROM nginx:alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
# Writes trusted-proxies/trusted-proxies.conf from TRUSTED_PROXIES before nginx starts.
COPY --chmod=755 docker/trusted-proxies.sh /docker-entrypoint.d/40-trusted-proxies.sh
COPY --from=build /app/dist /usr/share/nginx/html

# Runs as the unprivileged nginx user: no `user` switch, the pid file in /tmp, and only the folders nginx
# writes to owned by it.
RUN sed -i -e '/^user /d' -e 's#^pid .*#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
    && install -d -o nginx -g nginx /etc/nginx/trusted-proxies \
    && chown -R nginx:nginx /var/cache/nginx
USER nginx

EXPOSE 8080