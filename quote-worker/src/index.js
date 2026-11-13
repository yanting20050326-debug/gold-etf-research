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

async function fetchTwse(now) {
  if (!isTwseSession(now)) return {};
  const exCh = TWSE_CODES.map((code) => `tse_${code}.tw`).join("|");
  const url = `${TWSE_URL}?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0`;
  return parseTwsePayload(await fetchJson(url));
}

async function fetchIntlGold() {
  return parseYahooChart(await fetchJson(YAHOO_URL));
}

// 各來源獨立失敗：TWSE 掛了不影響國際金價，反之亦然。
export async function loadPayload(now = new Date()) {
  const [twse, gold] = await Promise.allSettled([fetchTwse(now), fetchIntlGold()]);
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

  const [payload, site] = await Promise.all([loadPayload(now), fetchSiteData()]);
  const summary = await runNotifyCheck({
    today: formatTaipeiDate(now),
    quotes: payload.quotes,
    site,
    kv: env.NOTIFY_STATE,
    sendLine: makeLineSender(env.LINE_CHANNEL_ACCESS_TOKEN),
  });
  if (summary.sent || summary.failed) console.log("notify check:", JSON.stringify(summary));
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(handleScheduled(event, env));
  },

  async fetch(request, env, ctx) {
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

    const payload = await loadPayload();
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
