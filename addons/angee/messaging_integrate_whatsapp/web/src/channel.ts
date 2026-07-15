/**
 * The two names this addon binds itself to a channel with, kept in their own
 * module so the pairing dialog and the verb that opens it can both read them
 * without importing each other.
 */

/** The model this addon's channel verbs are contributed against. */
export const CHANNEL_MODEL = "messaging.Channel";

/**
 * The `backend_class` impl key this addon registers its channel backend under —
 * the same key `ANGEE_CHANNEL_BACKEND_CLASSES` maps to `WhatsAppChannelBackend`,
 * and the fact a WhatsApp-backed row carries. Verb contributions are keyed on it
 * so they reach this addon's channels and no others.
 */
export const WHATSAPP_BACKEND = "whatsapp";
