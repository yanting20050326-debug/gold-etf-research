// 即時報價 Worker：收到請求時才去抓 TWSE 與 Yahoo，快取 CACHE_SECONDS 秒。
//
// 為什麼不用 GitHub Actions 抓價：Actions 的 `schedule` 是盡力而為、實測常
// 延遲數小時，而且每次都要重新建站。報價只是一個小 JSON，直接由 Worker
// 即時提供，更新頻率就只取決於網頁的輪詢間隔，跟 GitHub 排程完全脫鉤。
//
// 報價端點（fetch）不需要任何 secret：兩個資料來源都是公開端點。
//
// 另外每分鐘有一個排程（scheduled）：用即時價判斷是否到了加碼點，到了就發
// LINE 廣播（見 notify.js）。這部分需要兩樣東西，都不會寫進程式碼或 repo：
//   - secret LINE_CHANNEL_ACCESS_TOKEN（wrangler secret put）
//   - KV namespace 綁定 NOTIFY_STATE（存「上次通知到哪個階段」）
// 沒有設定 token 時排程會直接略過，不影響報價端點。

import {
  TWSE_CODES,
  buildPayload,
  formatTaipeiDate,
  isTwseSession,
  parseTwsePayload,
  parseYahooChart,
} from "./quotes.js";
import { runNotifyCheck } from "./notify.js";
import {
  AI_MODEL,
  checkRateLimit,
  isAllowedOrigin,
  runConversation,
  validateChatRequest,
} from "./assistant.js";
import { extractPlanDraft, validatePlanDraftRequest } from "./plan_draft.js";

const SITE_DATA_URL =
  "https://yanting20050326-debug.github.io/gold-research-site/data/gold_site.json";
const LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast";
const CACHE_SECONDS = 10;
const UPSTREAM_TIMEOUT_MS = 8000;
const TWSE_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp";
const YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

async function fetchJson(url) {
  const response = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0" },
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// ---- TWSE：重試 + 失敗時沿用上一筆好價 ----------------------------------------
//
// TWSE 前面的 CDN 偶爾會對 Cloudflare 機房出去的請求回 520/502（實測約半小時內零星出現）。
// 單次失敗就放棄的話，ETF 即時價會直接消失；所以先重試，還是失敗就用上一次成功的報價
// （保留原本的成交時間，網頁會顯示那個時間，不會假裝是剛抓到的）。只沿用「同一天、2 小時內」的，
// 不會把昨天的價格當成今天的。

const TWSE_ATTEMPTS = 3;
const TWSE_ATTEMPT_TIMEOUT_MS = 4000;
const TWSE_STALE_MAX_AGE_MS = 2 * 60 * 60 * 1000;
const TWSE_KV_KEY = "twse:last_good";
const TWSE_KV_TTL_SECONDS = 2 * 60 * 60;
const TWSE_KV_WRITE_INTERVAL_MS = 5 * 60 * 1000; // KV 免費額度每天只有 1,000 次寫入，所以最多每 5 分鐘寫一次
const TWSE_HEADERS = {
  "User-Agent": "Mozilla/5.0",
  Accept: "application/json, text/javascript, */*; q=0.01",
  "Accept-Language": "zh-TW,zh;q=0.9",
  Referer: "https://mis.twse.com.tw/stock/index.jsp",
};

let lastGoodTwse = null; // { savedAt, date, quotes }：同一個 isolate 內的快取
let lastKvWriteAt = 0;

export function resetTwseMemory() {
  lastGoodTwse = null;
  lastKvWriteAt = 0;
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchTwseOnce(url) {
  const response = await fetch(url, {
    headers: TWSE_HEADERS,
    signal: AbortSignal.timeout(TWSE_ATTEMPT_TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function fetchTwseWithRetry(url, sleep) {
  let lastError;
  for (let attempt = 1; attempt <= TWSE_ATTEMPTS; attempt++) {
    try {
      const quotes = parseTwsePayload(await fetchTwseOnce(url));
      if (Object.keys(quotes).length > 0) return quotes;
      lastError = new Error("TWSE returned no usable quotes");
    } catch (error) {
      lastError = error;
    }
    if (attempt < TWSE_ATTEMPTS) await sleep(300 * attempt);
  }
  throw lastError;
}

async function rememberTwse(quotes, now, kv) {
  lastGoodTwse = { savedAt: now.getTime(), date: formatTaipeiDate(now), quotes };
  if (!kv || now.getTime() - lastKvWriteAt < TWSE_KV_WRITE_INTERVAL_MS) return;
  lastKvWriteAt = now.getTime();
  try {
    await kv.put(TWSE_KV_KEY, JSON.stringify(lastGoodTwse), { expirationTtl: TWSE_KV_TTL_SECONDS });
  } catch (error) {
    console.error("TWSE last-good save failed:", String(error));
  }
}

async function recallTwse(now, kv) {
  let best = lastGoodTwse;
  if (kv) {
    try {
      const stored = await kv.get(TWSE_KV_KEY, "json");
      if (stored && (!best || stored.savedAt > best.savedAt)) best = stored;
    } catch (error) {
      console.error("TWSE last-good read failed:", String(error));
    }
  }
  if (!best || best.date !== formatTaipeiDate(now)) return null;
  if (now.getTime() - best.savedAt > TWSE_STALE_MAX_AGE_MS) return null;
  return best.quotes;
}

async function fetchTwse(now, { kv, sleep = delay } = {}) {
  if (!isTwseSession(now)) return {};
  const exCh = TWSE_CODES.map((code) => `tse_${code}.tw`).join("|");
  const url = `${TWSE_URL}?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0`;
  try {
    const quotes = await fetchTwseWithRetry(url, sleep);
    await rememberTwse(quotes, now, kv);
    return quotes;
  } catch (error) {
    const stale = await recallTwse(now, kv);
    if (!stale) throw error;
    console.error("TWSE failed, serving last good quotes:", String(error));
    return stale;
  }
}

async function fetchIntlGold() {
  return parseYahooChart(await fetchJson(YAHOO_URL));
}

// 各來源獨立失敗：TWSE 掛了不影響國際金價，反之亦然。
export async function loadPayload(now = new Date(), options = {}) {
  const [twse, gold] = await Promise.allSettled([fetchTwse(now, options), fetchIntlGold()]);
  if (twse.status === "rejected") console.error("TWSE failed:", String(twse.reason));
  if (gold.status === "rejected") console.error("Yahoo failed:", String(gold.reason));
  return buildPayload(
    twse.status === "fulfilled" ? twse.value : {},
    gold.status === "fulfilled" ? gold.value : null,
    now,
  );
}

// 最近一次建站結果（回檔高點、MA20、起漲前低等）。公開檔案，邊緣快取 5 分鐘。
async function fetchSiteData() {
  const response = await fetch(SITE_DATA_URL, {
    signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    cf: { cacheTtl: 300, cacheEverything: true },
  });
  if (!response.ok) throw new Error(`site data HTTP ${response.status}`);
  return response.json();
}

function makeLineSender(token) {
  return async (text) => {
    try {
      const response = await fetch(LINE_BROADCAST_URL, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ messages: [{ type: "text", text }] }),
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      });
      if (!response.ok) console.error("LINE broadcast failed:", response.status);
      return response.ok;
    } catch (error) {
      console.error("LINE broadcast error:", String(error));
      return false;
    }
  };
}

export async function handleScheduled(event, env) {
  if (!env.LINE_CHANNEL_ACCESS_TOKEN || !env.NOTIFY_STATE) {
    console.warn("LINE token or NOTIFY_STATE KV not configured; skipping notify check");
    return;
  }
  const now = new Date(event.scheduledTime);
  // 台股盤中每分鐘檢查；其餘時間（只有國際金價在動）每 5 分鐘檢查一次就夠。
  if (!isTwseSession(now) && now.getUTCMinutes() % 5 !== 0) return;

  const [payload, site] = await Promise.all([loadPayload(now, { kv: env.NOTIFY_STATE }), fetchSiteData()]);
  const summary = await runNotifyCheck({
    today: formatTaipeiDate(now),
    quotes: payload.quotes,
    site,
    kv: env.NOTIFY_STATE,
    sendLine: makeLineSender(env.LINE_CHANNEL_ACCESS_TOKEN),
  });
  if (summary.sent || summary.failed) console.log("notify check:", JSON.stringify(summary));
}

// ---- AI 小幫手 /chat ----------------------------------------------------------
//
// 用 Cloudflare Workers AI（wrangler.jsonc 的 `ai` binding，名稱 AI），不需要任何金鑰；
// 沒有 binding 時回 503 not_configured，網頁會顯示「小幫手尚未啟用」，報價端點完全不受影響。
// 只接受本站網址（見 assistant.js 的 ALLOWED_ORIGINS）發出的請求，並有每人／每日次數上限。

function jsonResponse(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

function chatCorsHeaders(origin) {
  return isAllowedOrigin(origin)
    ? { "Access-Control-Allow-Origin": origin, Vary: "Origin" }
    : { Vary: "Origin" };
}

const AI_TIMEOUT_MS = 25000;

async function callWorkersAi(ai, body) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("workers_ai_timeout")), AI_TIMEOUT_MS);
  });
  try {
    return await Promise.race([ai.run(AI_MODEL, body), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

// /chat 與 /plan 共用的關卡：CORS 預檢、只收 POST、只收本站來源、要有 AI binding、body 是 JSON。
// 通過回 { cors, body }；沒通過回 { response }（直接回給瀏覽器）。
async function guardAssistantRequest(request, env) {
  const origin = request.headers.get("Origin");
  const cors = chatCorsHeaders(origin);
  if (request.method === "OPTIONS") {
    return {
      response: new Response(null, {
        status: 204,
        headers: {
          ...cors,
          "Access-Control-Allow-Methods": "POST, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type",
          "Access-Control-Max-Age": "86400",
        },
      }),
    };
  }
  if (request.method !== "POST") return { response: jsonResponse({ error: "method_not_allowed" }, 405, cors) };
  if (!isAllowedOrigin(origin)) return { response: jsonResponse({ error: "forbidden_origin" }, 403, cors) };
  if (!env.AI) return { response: jsonResponse({ error: "not_configured" }, 503, cors) };
  try {
    return { cors, body: await request.json() };
  } catch {
    return { response: jsonResponse({ error: "bad_request" }, 400, cors) };
  }
}

// 次數上限兩個端點共用（同一個 KV 計數），超過回 429 的回應，否則回 null。
async function rateLimitResponse(request, env, cors) {
  const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
  const limit = await checkRateLimit(env.NOTIFY_STATE, ip);
  return limit.ok ? null : jsonResponse({ error: limit.error }, 429, cors);
}

// 「用一句話建立購買計畫」：模型把一句話轉成計畫欄位，plan_draft.js 逐欄驗證後回傳。
export async function handlePlanDraft(request, env) {
  const guard = await guardAssistantRequest(request, env);
  if (guard.response) return guard.response;
  const { cors, body } = guard;
  const checked = validatePlanDraftRequest(body);
  if (!checked.ok) return jsonResponse({ error: checked.error }, 400, cors);
  const limited = await rateLimitResponse(request, env, cors);
  if (limited) return limited;
  try {
    const result = await extractPlanDraft({ text: checked.text, callModel: (modelBody) => callWorkersAi(env.AI, modelBody) });
    return jsonResponse(result, 200, cors);
  } catch (error) {
    console.error("plan draft failed:", String(error?.message ?? error));
    return jsonResponse({ error: "upstream_error" }, 502, cors);
  }
}

export async function handleChat(request, env) {
  const guard = await guardAssistantRequest(request, env);
  if (guard.response) return guard.response;
  const { cors, body } = guard;
  const checked = validateChatRequest(body);
  if (!checked.ok) return jsonResponse({ error: checked.error }, 400, cors);
  const limited = await rateLimitResponse(request, env, cors);
  if (limited) return limited;

  try {
    const site = await fetchSiteData();
    const reply = await runConversation({
      messages: checked.messages,
      site,
      focusCode: checked.code,
      callModel: (modelBody) => callWorkersAi(env.AI, modelBody),
    });
    return jsonResponse({ reply }, 200, cors);
  } catch (error) {
    console.error("chat failed:", String(error?.message ?? error));
    return jsonResponse({ error: "upstream_error" }, 502, cors);
  }
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(handleScheduled(event, env));
  },

  async fetch(request, env, ctx) {
    const pathname = new URL(request.url).pathname;
    if (pathname === "/chat") return handleChat(request, env);
    if (pathname === "/plan") return handlePlanDraft(request, env);
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }
    if (request.method !== "GET") {
      return new Response("Method Not Allowed", { status: 405, headers: CORS_HEADERS });
    }

    const cache = caches.default;
    const cacheKey = new Request(new URL(request.url).origin + "/live_quotes.json");
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const payload = await loadPayload(new Date(), { kv: env.NOTIFY_STATE });
    const response = new Response(JSON.stringify(payload), {
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": `public, max-age=${CACHE_SECONDS}`,
      },
    });
    // 兩個來源都沒資料時不要快取，下一個請求馬上重試。
    if (Object.keys(payload.quotes).length > 0) {
      ctx.waitUntil(cache.put(cacheKey, response.clone()));
    }
    return response;
  },
};
