// Сравнение строк за постоянное время - защита от timing-атак на секреты
// (secret_token вебхука, секрет эндпоинта списка подписчиков).
export function timingSafeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;

  const bufA = new TextEncoder().encode(a);
  const bufB = new TextEncoder().encode(b);
  const len = Math.max(bufA.length, bufB.length, 1);

  let diff = bufA.length ^ bufB.length;
  for (let i = 0; i < len; i++) {
    const byteA = i < bufA.length ? bufA[i] : 0;
    const byteB = i < bufB.length ? bufB[i] : 0;
    diff |= byteA ^ byteB;
  }
  return diff === 0;
}
