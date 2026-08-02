# citymind-frontend — static SaaS dashboard served by nginx.
# Talks to citymind-backend directly over REST + WebSocket from the browser
# (configurable at runtime via Settings, persisted to localStorage) — no
# build step, no bundler: pages are loaded as native ES modules.
FROM nginx:1.27-alpine

COPY index.html /usr/share/nginx/html/index.html
COPY styles.css /usr/share/nginx/html/styles.css
COPY js/ /usr/share/nginx/html/js/
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080
