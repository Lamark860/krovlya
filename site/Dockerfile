# Сборка статики в контейнере: на хосте ничего ставить не нужно.
FROM oven/bun:1.3-alpine AS build
WORKDIR /app

# Слой зависимостей отдельно — при правке кода не переустанавливается.
# Повторы и низкая параллельность: канал бывает забит, и пакеты отваливаются
# по таймауту резолва — без ретраев сборка падает на случайном пакете.
COPY package.json bun.lock* ./
RUN set -e; \
    for i in 1 2 3 4 5; do \
      bun install --network-concurrency 4 && exit 0; \
      echo "попытка $i не удалась, повтор через 15 с"; \
      sleep 15; \
    done; \
    echo "не удалось установить зависимости: проверьте сеть"; exit 1

COPY . .

# Адрес сайта и разрешение на индексацию печатаются в статику на этапе сборки:
# canonical, OG и robots.txt после этого не меняются рестартом — только пересборкой.
ARG PUBLIC_SITE_URL=https://example.ru
ARG PUBLIC_INDEXABLE=0
ENV PUBLIC_SITE_URL=$PUBLIC_SITE_URL
ENV PUBLIC_INDEXABLE=$PUBLIC_INDEXABLE

RUN bun run build

# Отдаём готовую статику обычным nginx — ни Node, ни Bun в рантайме не нужны
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
