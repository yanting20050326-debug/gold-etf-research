import test from "node:test";
import assert from "node:assert/strict";
import {
  bestBookPrice,
  buildPayload,
  formatTaipeiMinute,
  isTwseSession,
  parseTwsePayload,
  parseTwseRow,
  parseYahooChart,
} from "../src/quotes.js";

test("bestBookPrice 取第一檔，'-' 與空值回 null", () => {
  assert.equal(bestBookPrice("44.55_44.60_"), 44.55);
  assert.equal(bestBookPrice("-"), null);
  assert.equal(bestBookPrice("-_-_"), null);
  assert.equal(bestBookPrice(""), null);
  assert.equal(bestBookPrice(undefined), null);
});

test("parseTwseRow：有成交價用 z", () => {
  const q = parseTwseRow({ z: "44.60", y: "45.80", a: "44.70_", b: "44.50_", t: "12:47:03" });
  assert.deepEqual(q, { close: 44.6, quote_time: "12:47:03", is_realtime: true });
});

test("parseTwseRow：z 是 '-' 時用買賣一中間價（00635U 整天都沒有 z）", () => {
  const q = parseTwseRow({ z: "-", y: "45.80", a: "44.60_44.65_", b: "44.50_44.45_", t: "10:00:00" });
  assert.equal(q.close, 44.55);
});

test("價格四捨五入到四位小數，去掉浮點雜訊", () => {
  const q = parseTwseRow({ z: "-", y: "40", a: "40.25_", b: "40.2_", t: "11:30:04" });
  assert.equal(q.close, 40.225);
  const ts = Date.UTC(2026, 10, 13, 3, 21) / 1000;
  const g = parseYahooChart({
    chart: { result: [{ timestamp: [ts], indicators: { quote: [{ close: [3920.10009765625] }] } }] },
  });
  assert.equal(g.close, 3920.1001);
});

test("parseTwseRow：z 與買賣價都沒有才退回昨收", () => {
  const q = parseTwseRow({ z: "-", y: "45.80", a: "-", b: "-", t: "09:00:00" });
  assert.equal(q.close, 45.8);
});

test("parseTwseRow：昨收無效回 null", () => {
  assert.equal(parseTwseRow({ z: "44", y: "-" }), null);
});

test("parseTwsePayload：rtmessage 非 OK 或沒資料回空物件", () => {
  assert.deepEqual(parseTwsePayload({ rtmessage: "FAIL", msgArray: [] }), {});
  assert.deepEqual(parseTwsePayload({ rtmessage: "OK", msgArray: [] }), {});
  assert.deepEqual(parseTwsePayload(null), {});
});

test("parseTwsePayload：多檔一次解析、壞掉的列被略過", () => {
  const out = parseTwsePayload({
    rtmessage: "OK",
    msgArray: [
      { c: "00635U", z: "44.6", y: "45.8", t: "10:00:00" },
      { c: "00708L", z: "-", y: "x" },
    ],
  });
  assert.deepEqual(Object.keys(out), ["00635U"]);
});

test("parseYahooChart 取最後一筆非 null 收盤並轉台北時間", () => {
  // 2026-11-12T23:31:00Z = 台北 2026-11-13 07:31
  const ts = Date.UTC(2026, 10, 12, 23, 31) / 1000;
  const q = parseYahooChart({
    chart: { result: [{ timestamp: [ts - 60, ts], indicators: { quote: [{ close: [3879, null] }] } }] },
  });
  assert.deepEqual(q, { close: 3879, quote_time: "2026-11-13 07:30", is_realtime: true });
});

test("parseYahooChart：格式異常回 null", () => {
  assert.equal(parseYahooChart({}), null);
  assert.equal(parseYahooChart({ chart: { result: [{ timestamp: [1], indicators: { quote: [{ close: [null] }] } }] } }), null);
});

test("formatTaipeiMinute 跨日", () => {
  assert.equal(formatTaipeiMinute(Date.UTC(2026, 10, 12, 16, 5) / 1000), "2026-11-13 00:05");
});

test("isTwseSession：平日 08:30–14:30（台北）", () => {
  // 2026-11-13 是週五
  assert.equal(isTwseSession(new Date("2026-11-13T00:29:00Z")), false); // 08:29
  assert.equal(isTwseSession(new Date("2026-11-13T00:30:00Z")), true); // 08:30
  assert.equal(isTwseSession(new Date("2026-11-13T03:25:00Z")), true); // 11:25
  assert.equal(isTwseSession(new Date("2026-11-13T06:30:00Z")), true); // 14:30
  assert.equal(isTwseSession(new Date("2026-11-13T06:31:00Z")), false); // 14:31
  assert.equal(isTwseSession(new Date("2026-11-14T03:00:00Z")), false); // 週六
  assert.equal(isTwseSession(new Date("2026-11-15T03:00:00Z")), false); // 週日
});

test("buildPayload 與 update_quotes.py 相同結構", () => {
  const now = new Date("2026-11-13T03:25:00Z");
  const p = buildPayload({ "00635U": { close: 1 } }, { close: 2 }, now);
  assert.equal(p.generated_at, "2026-11-13T03:25:00.000Z");
  assert.deepEqual(Object.keys(p.quotes), ["00635U", "GCF"]);
  assert.deepEqual(buildPayload({}, null, now).quotes, {});
});
