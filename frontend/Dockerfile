FROM oven/bun:1.2.21-slim

WORKDIR /app

COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile --production

COPY frontend/server.ts ./server.ts
COPY frontend/src ./src
COPY frontend/public ./public

ENV PORT=3000

EXPOSE 3000

USER bun

CMD ["bun", "run", "server.ts"]
