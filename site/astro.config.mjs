// @ts-check
import { defineConfig } from "astro/config";

// Статическая сборка: две посадочные страницы + юридические страницы.
//
// Адрес сайта нужен для canonical и OG. Постоянного домена ещё нет (блокер Б4),
// поэтому берём из окружения: на превью это адрес стенда, на боевом — домен.
// ВАЖНО: значение печётся на этапе сборки, смена адреса = пересборка, не рестарт.
export default defineConfig({
  site: process.env.PUBLIC_SITE_URL || "https://example.ru",
  output: "static",
  // format: "file" даёт /poly.html вместо /poly/index.html — тогда адрес без слэша
  // отдаётся сразу, без лишнего 301 на каждую посадочную.
  build: { inlineStylesheets: "auto", format: "file" },
  compressHTML: true,
});
