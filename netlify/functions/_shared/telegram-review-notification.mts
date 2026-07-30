import type {
  ContentLibraryDetail,
  PublicCatalogAsset,
} from "./content-catalog.mts";

const TELEGRAM_API_BASE = "https://api.telegram.org";
const TELEGRAM_CAPTION_LIMIT = 1_024;
const TELEGRAM_TEXT_LIMIT = 4_096;
const TELEGRAM_TIMEOUT_MS = 12_000;

export type TelegramReviewConfig = {
  botToken: string;
  chatId: string;
};

export type TelegramReviewResult = {
  sent: boolean;
  photoSent: boolean;
  textSent: boolean;
};

const CLIENT_NAMES: Record<ContentLibraryDetail["client_id"], string> = {
  babylon: "Babylon",
  origintrail: "OriginTrail",
  squid: "Squid",
  yellow: "Yellow",
};

const CONTENT_KIND_NAMES: Record<ContentLibraryDetail["content_kind"], string> = {
  article: "아티클",
  daily_news: "데일리 뉴스",
  tutorial: "튜토리얼",
};

function normalizedText(value: unknown, maximum: number): string {
  if (typeof value !== "string") return "";
  return value.replace(/\r\n?/g, "\n").trim().slice(0, maximum);
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringList(value: unknown, maximum = 5): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    .slice(0, maximum)
    .map((item) => item.replace(/\s+/g, " ").trim().slice(0, 240));
}

export function escapeTelegramHtml(value: string): string {
  return value.replace(/[&<>]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
  })[character] || character);
}

export function telegramReviewConfig(
  getEnv: (name: string) => string | undefined,
): TelegramReviewConfig | null {
  const botToken = (getEnv("TELEGRAM_REVIEW_BOT_TOKEN") || "").trim();
  const chatId = (getEnv("TELEGRAM_REVIEW_CHAT_ID") || "").trim();
  if (
    !/^[0-9]{6,14}:[A-Za-z0-9_-]{30,100}$/.test(botToken)
    || !/^-?[1-9][0-9]{5,19}$/.test(chatId)
  ) return null;
  return { botToken, chatId };
}

function fallbackReviewBody(detail: ContentLibraryDetail): string {
  const content = recordValue(detail.current_version.content);
  if (detail.content_kind === "article") {
    const lead = normalizedText(content.lead, 1_800);
    const takeaways = stringList(content.key_takeaways, 4);
    return [lead, takeaways.map((item) => `• ${item}`).join("\n")]
      .filter(Boolean)
      .join("\n\n");
  }
  if (detail.content_kind === "tutorial") {
    const series = recordValue(content.series);
    const subtitle = normalizedText(series.series_subtitle_kr, 800);
    const title = normalizedText(series.series_title_en, 300);
    return [subtitle || title, `총 ${detail.assets.length}장`]
      .filter(Boolean)
      .join("\n\n");
  }
  const spec = recordValue(content.spec);
  const headline = normalizedText(spec.headline, 300);
  const lines = stringList(spec.body_lines, 5);
  return [headline, lines.map((item) => `• ${item}`).join("\n")]
    .filter(Boolean)
    .join("\n\n");
}

export function buildTelegramReviewCaption(detail: ContentLibraryDetail): string {
  const brand = CLIENT_NAMES[detail.client_id];
  const kind = CONTENT_KIND_NAMES[detail.content_kind];
  const title = normalizedText(detail.title, 240);
  const caption = [
    `🔎 <b>검토 요청 · ${escapeTelegramHtml(brand)}</b>`,
    `${escapeTelegramHtml(kind)} · 게시 전 확인`,
    `<b>${escapeTelegramHtml(title)}</b>`,
    "배너와 발행 문구를 확인해 주세요.",
  ].join("\n");
  return caption.slice(0, TELEGRAM_CAPTION_LIMIT);
}

export function buildTelegramReviewMessage(detail: ContentLibraryDetail): string {
  const channelCopy = recordValue(detail.current_version.channel_copy);
  const reviewBody = normalizedText(channelCopy.telegram, 3_400)
    || fallbackReviewBody(detail);
  const prefix = "<b>게시할 Telegram 문구</b>\n\n";
  const suffix = "\n\n<i>개인 검토 알림입니다. 승인 전에는 어디에도 게시되지 않습니다.</i>";
  const available = Math.max(
    0,
    TELEGRAM_TEXT_LIMIT - prefix.length - suffix.length - 32,
  );
  return `${prefix}${escapeTelegramHtml(reviewBody.slice(0, available))}${suffix}`;
}

export function telegramReviewUrl(siteUrl: string, contentItemId: string): string {
  const url = new URL(siteUrl);
  url.pathname = "/";
  url.search = "";
  url.hash = "";
  url.searchParams.set("view", "library");
  url.searchParams.set("content", contentItemId);
  return url.toString();
}

function reviewPhoto(detail: ContentLibraryDetail): PublicCatalogAsset | null {
  return detail.assets.find((asset) => (
    asset.asset_kind === "png"
    && asset.mime_type === "image/png"
    && /^https:\/\//.test(asset.url)
  )) || null;
}

async function telegramPost(
  config: TelegramReviewConfig,
  method: "sendPhoto" | "sendMessage",
  body: BodyInit,
  contentType: string | null,
  fetcher: typeof fetch,
): Promise<boolean> {
  const headers = contentType ? { "Content-Type": contentType } : undefined;
  try {
    const response = await fetcher(
      `${TELEGRAM_API_BASE}/bot${config.botToken}/${method}`,
      {
        method: "POST",
        headers,
        body,
        signal: AbortSignal.timeout(TELEGRAM_TIMEOUT_MS),
      },
    );
    if (!response.ok) return false;
    const result = await response.json() as { ok?: unknown };
    return result.ok === true;
  } catch {
    return false;
  }
}

export async function sendTelegramReviewNotification(
  config: TelegramReviewConfig,
  detail: ContentLibraryDetail,
  siteUrl: string,
  banner: Blob | null = null,
  fetcher: typeof fetch = fetch,
): Promise<TelegramReviewResult> {
  let photoSent = false;
  const storedPhoto = reviewPhoto(detail);
  if (banner || storedPhoto) {
    const form = new FormData();
    form.set("chat_id", config.chatId);
    form.set("caption", buildTelegramReviewCaption(detail));
    form.set("parse_mode", "HTML");
    if (banner) {
      form.set("photo", banner, `${detail.client_id}-${detail.content_kind}-review.png`);
    } else if (storedPhoto) {
      form.set("photo", storedPhoto.url);
    }
    photoSent = await telegramPost(config, "sendPhoto", form, null, fetcher);
  }

  const messageBody = {
    chat_id: config.chatId,
    text: buildTelegramReviewMessage(detail),
    parse_mode: "HTML",
    link_preview_options: { is_disabled: true },
    reply_markup: {
      inline_keyboard: [[{
        text: "✅ 승인·수정 확인",
        url: telegramReviewUrl(siteUrl, detail.content_item_id),
      }]],
    },
  };
  const textSent = await telegramPost(
    config,
    "sendMessage",
    JSON.stringify(messageBody),
    "application/json",
    fetcher,
  );
  return {
    sent: textSent,
    photoSent,
    textSent,
  };
}
