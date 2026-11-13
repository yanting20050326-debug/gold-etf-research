// 即時報價 Worker：收到請求時才去抓 TWSE 與 Yahoo，快取 CACHE_SECONDS 秒。
//
// 為什麼不用 GitHub Actions 抓價：Actions 的 `schedule` 是盡力而為、實測常
// 延遲數小時，而且每次都要重新建站。報價只是一個小 JSON，直接由 Worker
// 即時提供，更新頻率就只取決於網頁的輪詢間隔，跟 GitHub 排程完全脫鉤。
//
// 不需要任何 secret：兩個資料來源都是公開端點。

import {
  TWSE_CODES,
  buildPayload,
  isTwseSession,
  parseTwsePayload,
  parseYahooChart,
} from "./quotes.js";

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

export default {
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
