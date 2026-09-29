/**
 * «Старая» цена для карточки товара.
 *
 * Размер надбавки и её основание — решение заказчика, записано во внутренних
 * документах проекта (вопрос 27).
 *
 * Надбавка считается ДЕТЕРМИНИРОВАННО — из названия товара, а не случайно:
 * со случайной величиной «старая цена» менялась бы при каждой пересборке сайта
 * и расходилась бы на витрине и в каталоге.
 */

const MIN = 10;   // проценты
const SPREAD = 16; // 10…25 включительно

/** FNV-1a: короткая, стабильная, одинаковая на сборке и в браузере. */
function hash(key: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i += 1) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Цена «до скидки», округлённая до десятков. */
export function strikePrice(key: string, price: number): number {
  const percent = MIN + (hash(key) % SPREAD);
  return Math.round((price * (100 + percent)) / 100 / 10) * 10;
}

/** Размер скидки в процентах — для плашки на карточке. */
export function discountPercent(key: string, price: number): number {
  const old = strikePrice(key, price);
  return Math.round(((old - price) / old) * 100);
}
