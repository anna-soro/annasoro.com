FROM nginx:1.27-alpine

RUN rm -rf /usr/share/nginx/html/*

# Build context is the repo root (see infra/docker-compose.yml: context: ..),
# so every path below is relative to the repo root, not to infra/.
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf

COPY app/*.html /usr/share/nginx/html/
COPY app/robots.txt /usr/share/nginx/html/robots.txt
COPY app/dist/ /usr/share/nginx/html/dist/
COPY app/site/ /usr/share/nginx/html/site/

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/index.html >/dev/null || exit 1
