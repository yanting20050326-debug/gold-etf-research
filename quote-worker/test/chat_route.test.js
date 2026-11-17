import test from "node:test";
import assert from "node:assert/strict";
import worker, { handleChat } from "../src/index.js";
import { LIMITS } from "../src/assistant.js";

const ORIGIN = "https://yanting20050326-debug.github.io";
const SITE = {
  generated_at: "2026-11-17T02:41:00+00:00",
  targets: {
    TBBGOLD: {
      code: "TBBGOLD",
      display_name: "台企銀黃金存摺",
      latest: { close: 4131, prev_close: 4108 },
      passbook_quote: { sell: 4131, buy: 4085, spread: 46, listed_at: "2026-11-17 10:30:20" },
      indicators: {},
    },
  },
};

function chatRequest(body, { origin = ORIGIN, method = "POST", raw } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (origin) headers.Origin = origin;
  return new Request("https://gold-quote-worker.example/chat", {
    method,
    headers,
    body: method === "GET" || method === "OPTIONS" ? undefined : raw ?? JSON.stringify(body),
  });
}

const VALID = { messages: [{ role: "user", content: "存摺和 ETF 哪個成本低？" }], code: "TBBGOLD" };

// 把 fetch 換成假的：站上資料與 Gemini 各回各的；用完還原。
async function withFetch(handler, fn) {
  const original = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), init });
    return handler(String(url), init);
  };
  try {
    return await fn(seen);
  } finally {
    globalThis.fetch = original;
  }
}

const okJson = (body) => new Response(JSON.stringify(body), { status: 200 });
const geminiText = (text) => okJson({ candidates: [{ content: { parts: [{ text }] } }] });
const routeFetch = (geminiResponse) => (url) =>
  url.includes("gold_site.json") ? okJson(SITE) : geminiResponse(url);

test("OPTIONS：本站來源拿到 CORS 許可，其他來源沒有", async () => {
  const ok = await handleChat(chatRequest(null, { method: "OPTIONS" }), {});
  assert.equal(ok.status, 204);
  assert.equal(ok.headers.get("Access-Control-Allow-Origin"), ORIGIN);
  assert.match(ok.headers.get("Access-Control-Allow-Methods"), /POST/);

  const bad = await handleChat(chatRequest(null, { method: "OPTIONS", origin: "https://evil.example" }), {});
  assert.equal(bad.headers.get("Access-Control-Allow-Origin"), null);
});

test("非本站來源與非 POST 都被拒絕", async () => {
  const env = { GEMINI_API_KEY: "k" };
  assert.equal((await handleChat(chatRequest(VALID, { origin: "https://evil.example" }), env)).status, 403);
  assert.equal((await handleChat(chatRequest(VALID, { origin: null }), env)).status, 403);
  assert.equal((await handleChat(chatRequest(null, { method: "GET" }), env)).status, 405);
});

test("沒設定 GEMINI_API_KEY：回 503 not_configured，網頁據此顯示『尚未啟用』", async () => {
  const res = await handleChat(chatRequest(VALID), {});
  assert.equal(res.status, 503);
  assert.deepEqual(await res.json(), { error: "not_configured" });
});

test("壞掉的 JSON 或不合法內容回 400", async () => {
  const env = { GEMINI_API_KEY: "k" };
  assert.equal((await handleChat(chatRequest(null, { raw: "{not json" }), env)).status, 400);
  assert.equal((await handleChat(chatRequest({ messages: [] }), env)).status, 400);
  const tooLong = { messages: [{ role: "user", content: "x".repeat(LIMITS.maxMessageChars + 1) }] };
  const res = await handleChat(chatRequest(tooLong), env);
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, "message_too_long");
});

test("成功：回傳模型答案，金鑰放在標頭而不是網址", async () => {
  await withFetch(routeFetch(() => geminiText("存摺進出一趟約 1.11%。")), async (seen) => {
    const res = await handleChat(chatRequest(VALID), { GEMINI_API_KEY: "SECRET-KEY-123" });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    assert.deepEqual(await res.json(), { reply: "存摺進出一趟約 1.11%。" });

    const gemini = seen.find((c) => c.url.includes("generativelanguage"));
    assert.equal(gemini.init.headers["x-goog-api-key"], "SECRET-KEY-123");
    assert.ok(!gemini.url.includes("SECRET-KEY-123"), "金鑰不能出現在網址");
    const body = JSON.parse(gemini.init.body);
    assert.ok(body.systemInstruction.parts[0].text.includes("台企銀黃金存摺"));
    assert.equal(body.contents[0].parts[0].text, "存摺和 ETF 哪個成本低？");
  });
});

test("超過次數：回 429", async () => {
  const kv = { get: async () => String(LIMITS.perIpMaxRequests), put: async () => {} };
  const res = await handleChat(chatRequest(VALID), { GEMINI_API_KEY: "k", NOTIFY_STATE: kv });
  assert.equal(res.status, 429);
  assert.equal((await res.json()).error, "rate_limited");
});

test("Gemini 出錯：回 502，日誌與回應都不含金鑰", async () => {
  const logged = [];
  const originalError = console.error;
  console.error = (...args) => logged.push(args.join(" "));
  try {
    await withFetch(routeFetch(() => new Response("boom", { status: 500 })), async () => {
      const res = await handleChat(chatRequest(VALID), { GEMINI_API_KEY: "SECRET-KEY-123" });
      assert.equal(res.status, 502);
      assert.equal((await res.json()).error, "upstream_error");
    });
  } finally {
    console.error = originalError;
  }
  assert.ok(logged.length > 0);
  assert.ok(logged.every((line) => !line.includes("SECRET-KEY-123")));
});

test("worker.fetch：/chat 走助理路由；其他路徑的 OPTIONS 仍是報價端點的 CORS 行為", async () => {
  const res = await worker.fetch(chatRequest(VALID), {}, { waitUntil() {} });
  assert.equal(res.status, 503); // 沒金鑰
  const preflight = await worker.fetch(
    new Request("https://gold-quote-worker.example/", { method: "OPTIONS" }),
    {},
    { waitUntil() {} }
  );
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("Access-Control-Allow-Origin"), "*");
});
