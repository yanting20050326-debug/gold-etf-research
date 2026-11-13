// 純函式：解析 TWSE / Yahoo 回應、判斷交易時段。不碰網路，方便單元測試。
// 邏輯一對一移植自 src/gold_research/fetch_twse.py 與 fetch_macro.py，
// 輸出格式與 update_quotes.py 的 live_quotes.json 完全相同。

export const TWSE_CODES = ["00635U", "00708L"];
export const INTL_GOLD_CODE = "GCF";

const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000; // 台北固定 UTC+8、沒有日光節約

// 把 Date 轉成「台北時間的欄位」（用 UTC getter 讀位移後的時間）。
function taipeiParts(date) {
  const shifted = new Date(date.getTime() + TAIPEI_OFFSET_MS);
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
    hour: shifted.getUTCHours(),
    minute: shifted.getUTCMinutes(),
    weekday: shifted.getUTCDay(), // 0=週日, 6=週六
  };
}

const pad = (n) => String(n).padStart(2, "0");

// 中間價 (a+b)/2 與 Yahoo 的 float32 會帶出 40.224999… 這種浮點雜訊，四位小數就夠。
const roundPrice = (value) => Math.round(value * 10000) / 10000;

// 平日台北 08:30–14:30（含開盤前／收盤後緩衝），對應 TWSE_SESSION_START/END。
export function isTwseSession(date) {
  const p = taipeiParts(date);
  if (p.weekday === 0 || p.weekday === 6) return false;
  const minutes = p.hour * 60 + p.minute;
  return minutes >= 8 * 60 + 30 && minutes <= 14 * 60 + 30;
}

// 台北日曆日（YYYY-MM-DD），通知去重用「同一天」判斷。
export function formatTaipeiDate(date) {
  const p = taipeiParts(date);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

export function formatTaipeiMinute(epochSeconds) {
  const p = taipeiParts(new Date(epochSeconds * 1000));
  return `${p.year}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`;
}

// 五檔字串（底線分隔，第一個是最佳價）取最佳價；"-"、空值回 null。
export function bestBookPrice(bookStr) {
  if (!bookStr) return null;
  const first = bookStr.split("_")[0];
  if (!first || first === "-") return null;
  const value = Number(first);
  return Number.isFinite(value) ? value : null;
}

// 一列 TWSE msgArray → {close, quote_time, is_realtime}；無法解析回 null。
// 優先順序：成交價 z（"-" 代表尚無成交）→ 買賣一中間價 → 昨收 y。
export function parseTwseRow(row) {
  const previousClose = Number(row.y);
  if (!Number.isFinite(previousClose)) return null;

  let price;
  const z = row.z;
  if (z && z !== "-" && Number.isFinite(Number(z))) {
    price = Number(z);
  } else {
    const ask = bestBookPrice(row.a);
    const bid = bestBookPrice(row.b);
    if (ask !== null && bid !== null) {
      price = (ask + bid) / 2;
    } else {
      if (!row.y || row.y === "-") return null;
      price = previousClose;
    }
  }
  return { close: roundPrice(price), quote_time: row.t ?? "", is_realtime: true };
}

export function parseTwsePayload(payload) {
  const quotes = {};
  if (!payload || payload.rtmessage !== "OK") return quotes;
  for (const row of payload.msgArray ?? []) {
    const code = row.c;
    if (!code) continue;
    const parsed = parseTwseRow(row);
    if (parsed) quotes[code] = parsed;
  }
  return quotes;
}

// Yahoo chart 回應 → 最新一筆非 null 的分鐘收盤；沒有則回 null。
export function parseYahooChart(payload) {
  const result = payload?.chart?.result?.[0];
  const timestamps = result?.timestamp;
  const closes = result?.indicators?.quote?.[0]?.close;
  if (!Array.isArray(timestamps) || !Array.isArray(closes)) return null;
  for (let i = Math.min(timestamps.length, closes.length) - 1; i >= 0; i--) {
    const close = closes[i];
    if (close !== null && close !== undefined && Number.isFinite(Number(close))) {
      return {
        close: roundPrice(Number(close)),
        quote_time: formatTaipeiMinute(timestamps[i]),
        is_realtime: true,
      };
    }
  }
  return null;
}

export function buildPayload(twseQuotes, intlGold, now) {
  const quotes = { ...twseQuotes };
  if (intlGold) quotes[INTL_GOLD_CODE] = intlGold;
  return { generated_at: now.toISOString(), quotes };
}
