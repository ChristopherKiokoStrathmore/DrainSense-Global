# DrainSense desk (Next.js). In production Caddy serves the API on the same origin, so the
# default NEXT_PUBLIC_API_URL is empty and the browser calls /api/v1/... relative to the page.
# NEXT_PUBLIC_* values are baked in at build time.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG NEXT_PUBLIC_API_URL=""
# Optional CARTO basemaps key (free, carto.com/basemaps/apikey). Not a secret: it ships to every browser.
ARG NEXT_PUBLIC_CARTO_KEY=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_PUBLIC_CARTO_KEY=$NEXT_PUBLIC_CARTO_KEY NEXT_TELEMETRY_DISABLED=1
RUN npm run build && npm prune --omit=dev

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/package.json /app/next.config.js ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
USER node
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "-p", "3000", "-H", "0.0.0.0"]
