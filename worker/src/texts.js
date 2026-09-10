import { CHANNEL_USERNAME } from './config.js';

const channelHandle = CHANNEL_USERNAME.replace(/^@/, '');
const channelUrl = `https://t.me/${channelHandle}`;
const channelLink = `<a href="${channelUrl}">Приватный эфир</a>`;

export const WELCOME_TEXT = `👋 Готово, ты подключён.

Сюда прилетают вакансии продакт-менеджера - джуны и стажировки, Россия и удалёнка. Собираю с Хабр Карьеры и профильных телеграм-каналов, отбираю нейросетью и присылаю только то, что похоже на старт в профессии.

📬 Если находок нет - молчу, спамить не буду.

/help - что я умею`;

export const ACCESS_DENIED_TEXT = `🔒 Доступ к боту - через канал ${channelLink}.

Подпишись и нажми /start ещё раз - всё откроется.`;

export const ALREADY_ACTIVE_TEXT = `✅ Так ты же уже подключён. Жду вакансий, как появятся.`;

export const STOPPED_TEXT = `🔕 Усё, рассылки больше не приходят. Передумаешь - жми /start.`;

export const HELP_TEXT = `🤖 Я приношу вакансии продакта - джуны и стажировки, Россия и удалёнка.

/start - подключить рассылку
/stop - отключить
/help - это сообщение`;

export const UNKNOWN_TEXT = `Я реагирую только на команды 🤷 Загляни в /help - там всё, что я умею.`;

export const CHECK_FAILED_TEXT = `⚠️ Не смог проверить доступ, попробуй ещё раз через минуту.`;

export const FLOOD_WARNING_TEXT = `⏳ Слишком часто, подожди немного.`;

export const STORAGE_FAILED_TEXT = `⚠️ Что-то сломалось, попробуй ещё раз через минуту.`;

export const REVOKED_TEXT = `⏸️ Доступ к рассылке приостановлен. Вернуть - /start.`;
