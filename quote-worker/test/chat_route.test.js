import test from "node:test";
import assert from "node:assert/strict";
import worker, { handleChat, handlePlanDraft } from "../src/index.js";
import { AI_MODEL, LIMITS } from "../src/assistant.js";

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

// 把 fetch 換成假的（只有站上資料會用到 fetch）；用完還原。
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
const siteOnly = (url) => (url.includes("gold_site.json") ? okJson(SITE) : new Response("unexpected", { status: 500 }));

// 假的 Workers AI binding：記下每次 run(model, body)，回 OpenAI 格式。
function fakeAi(handler = () => aiText("好的。")) {
  const calls = [];
  return {
    calls,
    run: async (model, body) => {
      calls.push({ model, body });
      return handler(model, body);
    },
  };
}
const aiText = (content) => ({ choices: [{ message: { role: "assistant", content } }] });

test("OPTIONS：本站來源拿到 CORS 許可，其他來源沒有", async () => {
  const ok = await handleChat(chatRequest(null, { method: "OPTIONS" }), {});
  assert.equal(ok.status, 204);
  assert.equal(ok.headers.get("Access-Control-Allow-Origin"), ORIGIN);
  assert.match(ok.headers.get("Access-Control-Allow-Methods"), /POST/);

  const bad = await handleChat(chatRequest(null, { method: "OPTIONS", origin: "https://evil.example" }), {});
  assert.equal(bad.headers.get("Access-Control-Allow-Origin"), null);
});

test("非本站來源與非 POST 都被拒絕", async () => {
  const env = { AI: fakeAi() };
  assert.equal((await handleChat(chatRequest(VALID, { origin: "https://evil.example" }), env)).status, 403);
  assert.equal((await handleChat(chatRequest(VALID, { origin: null }), env)).status, 403);
  assert.equal((await handleChat(chatRequest(null, { method: "GET" }), env)).status, 405);
});

test("沒有 AI binding：回 503 not_configured，網頁據此顯示『尚未啟用』", async () => {
  const res = await handleChat(chatRequest(VALID), {});
  assert.equal(res.status, 503);
  assert.deepEqual(await res.json(), { error: "not_configured" });
});

test("壞掉的 JSON 或不合法內容回 400", async () => {
  const env = { AI: fakeAi() };
  assert.equal((await handleChat(chatRequest(null, { raw: "{not json" }), env)).status, 400);
  assert.equal((await handleChat(chatRequest({ messages: [] }), env)).status, 400);
  const tooLong = { messages: [{ role: "user", content: "x".repeat(LIMITS.maxMessageChars + 1) }] };
  const res = await handleChat(chatRequest(tooLong), env);
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, "message_too_long");
});

test("成功：回傳模型答案，用的是 Workers AI 模型與站上資料", async () => {
  const ai = fakeAi(() => aiText("存摺進出一趟約 1.11%。"));
  await withFetch(siteOnly, async () => {
    const res = await handleChat(chatRequest(VALID), { AI: ai });
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Access-Control-Allow-Origin"), ORIGIN);
    assert.deepEqual(await res.json(), { reply: "存摺進出一趟約 1.11%。" });
  });
  assert.equal(ai.calls.length, 1);
  assert.equal(ai.calls[0].model, AI_MODEL);
  const body = ai.calls[0].body;
  assert.equal(body.messages[0].role, "system");
  assert.ok(body.messages[0].content.includes("台企銀黃金存摺"));
  assert.equal(body.messages[body.messages.length - 1].content, "存摺和 ETF 哪個成本低？");
  assert.ok(body.messages[0].content.includes("【本題試算結果】")); // 成本題由程式先算好
});

test("超過次數：回 429，而且不會呼叫模型", async () => {
  const kv = { get: async () => String(LIMITS.perIpMaxRequests), put: async () => {} };
  const ai = fakeAi();
  const res = await handleChat(chatRequest(VALID), { AI: ai, NOTIFY_STATE: kv });
  assert.equal(res.status, 429);
  assert.equal((await res.json()).error, "rate_limited");
  assert.equal(ai.calls.length, 0);
});

test("Workers AI 出錯（含額度用完）：回 502 upstream_error，不洩漏錯誤細節", async () => {
  const logged = [];
  const originalError = console.error;
  console.error = (...args) => logged.push(args.join(" "));
  const ai = fakeAi(() => {
    throw new Error("3036: account limited INTERNAL-DETAIL");
  });
  try {
    await withFetch(siteOnly, async () => {
      const res = await handleChat(chatRequest(VALID), { AI: ai });
      assert.equal(res.status, 502);
      const text = await res.text();
      assert.equal(JSON.parse(text).error, "upstream_error");
      assert.ok(!text.includes("INTERNAL-DETAIL"));
    });
  } finally {
    console.error = originalError;
  }
  assert.ok(logged.length > 0);
});

test("worker.fetch：/chat 走助理路由；其他路徑的 OPTIONS 仍是報價端點的 CORS 行為", async () => {
  const res = await worker.fetch(chatRequest(VALID), {}, { waitUntil() {} });
  assert.equal(res.status, 503); // 沒有 AI binding
  const preflight = await worker.fetch(
    new Request("https://gold-quote-worker.example/", { method: "OPTIONS" }),
    {},
    { waitUntil() {} }
  );
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("Access-Control-Allow-Origin"), "*");
});

// ---- /plan：用一句話建立購買計畫 ----------------------------------------------

function planRequest(body, { origin = ORIGIN } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (origin) headers.Origin = origin;
  return new Request("https://gold-quote-worker.example/plan", { method: "POST", headers, body: JSON.stringify(body) });
}

test("/plan 成功：回傳驗證後的欄位，模型多給的鍵與不合法的值被丟掉", async () => {
  const ai = fakeAi(() =>
    aiText('{"purpose":"goal","horizon":"3to5","budget":"200,000","maxLossPct":10,"code":"GCF","advice":"全部買進"}')
  );
  const res = await handlePlanDraft(planRequest({ text: "五年後存教育基金，總預算 20 萬，最多虧一成" }), { AI: ai });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), ORIGIN);
  const body = await res.json();
  assert.deepEqual(body.draft, { purpose: "goal", horizon: "3to5", budget: 200000, maxLossPct: 10 });
  assert.equal(ai.calls[0].model, AI_MODEL);
  assert.equal(ai.calls[0].body.messages[1].content, "五年後存教育基金，總預算 20 萬，最多虧一成");
});

test("/plan 與 /chat 共用關卡：非本站來源 403、沒有 AI binding 503、空白或太長 400", async () => {
  const ai = fakeAi();
  assert.equal((await handlePlanDraft(planRequest({ text: "x" }, { origin: "https://evil.example" }), { AI: ai })).status, 403);
  assert.equal((await handlePlanDraft(planRequest({ text: "x" }), {})).status, 503);
  assert.equal((await handlePlanDraft(planRequest({ text: "   " }), { AI: ai })).status, 400);
  const tooLong = await handlePlanDraft(planRequest({ text: "x".repeat(301) }), { AI: ai });
  assert.equal(tooLong.status, 400);
  assert.equal((await tooLong.json()).error, "message_too_long");
  assert.equal(ai.calls.length, 0);
});

test("/plan 超過次數回 429 且不呼叫模型；模型回非 JSON 回 502", async () => {
  const kv = { get: async () => String(LIMITS.perIpMaxRequests), put: async () => {} };
  const ai = fakeAi();
  const limited = await handlePlanDraft(planRequest({ text: "我想存錢" }), { AI: ai, NOTIFY_STATE: kv });
  assert.equal(limited.status, 429);
  assert.equal(ai.calls.length, 0);

  const original = console.error;
  console.error = () => {};
  try {
    const bad = await handlePlanDraft(planRequest({ text: "我想存錢" }), { AI: fakeAi(() => aiText("抱歉，我不懂")) });
    assert.equal(bad.status, 502);
    assert.equal((await bad.json()).error, "upstream_error");
  } finally {
    console.error = original;
  }
});

test("worker.fetch：/plan 走計畫路由", async () => {
  const ai = fakeAi(() => aiText('{"budget":50000}'));
  const res = await worker.fetch(planRequest({ text: "5 萬" }), { AI: ai }, { waitUntil() {} });
  assert.equal(res.status, 200);
  assert.deepEqual((await res.json()).draft, { budget: 50000 });
});
