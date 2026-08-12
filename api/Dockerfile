# Зависимостей нет — только рантайм Bun. Поэтому и слоя с установкой нет:
# нечему падать при плохой сети и нечего обновлять из-за уязвимостей в пакетах.
FROM oven/bun:1.3-alpine
WORKDIR /app

COPY server.ts ./
RUN mkdir -p data files

ENV PORT=8105
EXPOSE 8105
CMD ["bun", "run", "server.ts"]
