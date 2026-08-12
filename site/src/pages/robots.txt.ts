import type { APIRoute } from "astro";

// Пара к мета-тегу robots в Base.astro. Мета закрывает страницу от индексации,
// robots.txt — от обхода целиком, включая PDF лид-магнитов и /api/.
// Пока стоит PUBLIC_INDEXABLE=0 (стенд), на боевом домене выставляем 1.
const indexable = import.meta.env.PUBLIC_INDEXABLE === "1";

const body = indexable
  ? ["User-agent: *", "Allow: /", "Disallow: /api/", ""].join("\n")
  : ["User-agent: *", "Disallow: /", ""].join("\n");

export const GET: APIRoute = () =>
  new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
