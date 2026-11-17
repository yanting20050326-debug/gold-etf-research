import test from "node:test";
import assert from "node:assert/strict";
import {
  LIMITS,
  buildSnapshot,
  buildSystemPrompt,
  calcSplitPlan,
  checkRateLimit,
  compareCosts,
  isAllowedOrigin,
  runConversation,
  runTool,
  validateChatRequest,
} from "../src/assistant.js";

function target(overrides = {}) {
  return {
    code: "00635U",
    display_name: "元大S&P黃金",
    pullback_scale: 1,
    latest: { close: 39.6, prev_close: 39.74, is_realtime: true },
    indicators: {
      rsi14: 36.04,
      ma20: 41.52,
      bollinger: { lower: 39.6, upper: 43.43 },
      macd: { macd: -0.72, signal: -0.7 },
      volatility_squeeze: { ratio: 0.41 },
      relative_position: { position: 0.0 },
      technical_score: { composite: 74, label: "技術面偏冷" },
      pullback_stage: { stage: "加碼 20%（前提：出現止跌）", pullback_pct: 8.0, recent_high: 43.18, hint: "已達較大回檔" },
      prior_low: null,
      prior_low_broken: true,
      stage_signals: { trend_intact: false, momentum_stabilizing: false, macd_golden_cross: false },
    },
    ai_summary: { text: "今日摘要" },
    ...overrides,
  };
}

function site() {
  return {
    generated_at: "2026-11-17T02:41:00+00:00",
    targets: {
      "00635U": target(),
      "00708L": target({
        code: "00708L",
        display_name: "期元大S&P黃金正2",
        pullback_scale: 2,
        latest: { close: 61.1, prev_close: 61.25, is_realtime: true },
        indicators: { ...target().indicators, pullback_stage: { stage: "觀察區（尚未回檔到位）", pullback_pct: 1.0, recent_high: 70, hint: "x" } },
      }),
      GCF: target({ code: "GCF", display_name: "國際盤黃金（COMEX）", latest: { close: 3868.8, prev_close: 3853 } }),
      TBBGOLD: target({
        code: "TBBGOLD",
        display_name: "台企銀黃金存摺",
        price_digits: 0,
        latest: { close: 4131, prev_close: 4108, is_realtime: false },
        passbook_quote: { sell: 4131, buy: 4085, spread: 46, listed_at: "2026-11-17 10:30:20" },
        indicators: { ...target().indicators, pullback_stage: { stage: "加碼 20%（前提：出現止跌）", pullback_pct: 6.18, recent_high: 4403, hint: "h" } },
      }),
    },
  };
}

test("isAllowedOrigin：只放行本站與本機預覽", () => {
  assert.equal(isAllowedOrigin("https://yanting20050326-debug.github.io"), true);
  assert.equal(isAllowedOrigin("http://localhost:4321"), true);
  assert.equal(isAllowedOrigin("https://evil.example"), false);
  assert.equal(isAllowedOrigin(null), false);
  assert.equal(isAllowedOrigin("https://yanting20050326-debug.github.io.evil.example"), false);
});

test("validateChatRequest：接受合法對話並去除空白", () => {
  const out = validateChatRequest({ messages: [{ role: "user", content: "  RSI 是什麼？ " }], code: "GCF" });
  assert.deepEqual(out, { ok: true, messages: [{ role: "user", content: "RSI 是什麼？" }], code: "GCF" });
});

test("validateChatRequest：各種不合法輸入都被擋下", () => {
  const u = (content) => ({ role: "user", content });
  assert.equal(validateChatRequest(null).ok, false);
  assert.equal(validateChatRequest({ messages: [] }).ok, false);
  assert.equal(validateChatRequest({ messages: [{ role: "system", content: "x" }] }).ok, false);
  assert.equal(validateChatRequest({ messages: [u("")] }).ok, false);
  assert.equal(validateChatRequest({ messages: [{ role: "assistant", content: "hi" }] }).ok, false); // 最後一則必須是使用者
  assert.equal(validateChatRequest({ messages: [u("x".repeat(LIMITS.maxMessageChars + 1))] }).error, "message_too_long");
  assert.equal(validateChatRequest({ messages: Array(LIMITS.maxMessages + 1).fill(u("a")) }).error, "too_many_messages");
  // 小幫手自己的回覆可以比較長（分批計畫），但也有上限。
  const longReply = { role: "assistant", content: "y".repeat(LIMITS.maxAssistantChars) };
  assert.equal(validateChatRequest({ messages: [u("計畫？"), longReply, u("謝謝")] }).ok, true);
  const tooLongReply = { role: "assistant", content: "y".repeat(LIMITS.maxAssistantChars + 1) };
  assert.equal(validateChatRequest({ messages: [u("a"), tooLongReply, u("b")] }).error, "message_too_long");
  // 整段對話的總長度也有上限。
  const heavy = [u("x".repeat(500)), longReply, u("x".repeat(500)), longReply, u("x".repeat(500)), longReply, u("x")];
  assert.equal(validateChatRequest({ messages: heavy }).error, "message_too_long");
});

test("buildSnapshot：每個標的帶有現價、漲跌幅、階段與起漲前低狀態", () => {
  const snap = buildSnapshot(site());
  assert.equal(snap.標的.TBBGOLD.現價, 4131);
  assert.equal(snap.標的.TBBGOLD.今日漲跌幅_百分比, 0.56);
  assert.equal(snap.標的.TBBGOLD.台企銀牌價.價差, 46);
  assert.equal(snap.標的["00635U"].起漲前低, "已跌破所有前低（趨勢轉弱）");
  assert.equal(snap.標的["00635U"].價格類型, "盤中即時價");
  assert.equal(snap.標的.TBBGOLD.價格類型, "銀行牌價（本行賣出價）");
});

test("calcSplitPlan：台企銀存摺依 30 日高點與門檻算出觸發價與數量", () => {
  const plan = calcSplitPlan(site(), { code: "TBBGOLD", budget: 100000 });
  assert.equal(plan.三十日高點, 4403);
  const [first, add1, add2, last] = plan.分批;
  assert.equal(first.觸發價, 4271); // 4403 × 0.97，整數元
  assert.equal(add1.觸發價, 4227); // × 0.96
  assert.equal(add2.觸發價, 4139); // × 0.94
  assert.equal(last.觸發價, 4007); // × 0.91
  assert.deepEqual([first.投入金額, add1.投入金額, add2.投入金額, last.投入金額], [20000, 20000, 20000, 40000]);
  assert.equal(first.約可買數量, Math.floor(20000 / 4271));
  assert.equal(first.數量單位, "公克");
  assert.equal(first.目前狀態, "回檔幅度已到"); // 目前回檔 6.18%
  assert.equal(add2.目前狀態, "回檔幅度已到");
  assert.equal(last.目前狀態, "尚未到");
  assert.ok(plan.假設四批都在觸發價成交.平均成本 > 0);
  assert.equal(plan.分批獲利參考.length, 3);
});

test("calcSplitPlan：槓桿標的的門檻乘上倍數", () => {
  const plan = calcSplitPlan(site(), { code: "00708L", budget: 50000 });
  assert.deepEqual(plan.分批.map((s) => s.回檔門檻_百分比), [6, 8, 12, 18]);
  assert.equal(plan.分批[0].觸發價, 65.8); // 70 × 0.94
  assert.equal(plan.分批[0].數量單位, "股");
  assert.equal(plan.分批[0].目前狀態, "尚未到");
});

test("calcSplitPlan：國際金價、不存在的標的、不合理預算都回錯誤而不是亂算", () => {
  assert.match(calcSplitPlan(site(), { code: "GCF", budget: 1000 }).error, /無法在台灣直接買賣/);
  assert.match(calcSplitPlan(site(), { code: "NOPE", budget: 1000 }).error, /找不到標的/);
  assert.match(calcSplitPlan(site(), { code: "TBBGOLD", budget: 0 }).error, /預算/);
  assert.match(calcSplitPlan(site(), { code: "TBBGOLD", budget: "abc" }).error, /預算/);
});

test("compareCosts：存摺用價差、ETF 用手續費加證交稅，折扣會反映", () => {
  const out = compareCosts(site(), { amount: 100000 });
  assert.equal(out.台企銀黃金存摺.進出一趟成本_百分比, 1.11); // 46 / 4131
  assert.equal(out.台企銀黃金存摺.進出一趟成本_金額, 1114);
  assert.equal(out.黃金ETF.進出一趟成本_百分比, 0.385); // 0.1425×2 + 0.1
  assert.equal(out.黃金ETF.進出一趟成本_金額, 385);

  const discounted = compareCosts(site(), { amount: 100000, broker_fee_discount: 0.6 });
  assert.equal(discounted.黃金ETF.進出一趟成本_百分比, 0.271); // 0.1425×0.6×2 + 0.1
  assert.ok(discounted.黃金ETF.未計入.includes("經理費"));
});

test("compareCosts：預設金額、非法輸入與缺牌價", () => {
  assert.equal(compareCosts(site(), {}).投入金額, 100000);
  assert.match(compareCosts(site(), { amount: -5 }).error, /金額/);
  assert.match(compareCosts(site(), { broker_fee_discount: 6 }).error, /折扣/);
  assert.match(compareCosts({ targets: {} }, {}).error, /牌價/);
});

test("runTool：分派工具，未知工具回錯誤", () => {
  assert.ok(runTool("compare_costs", {}, site()).台企銀黃金存摺);
  assert.match(runTool("rm_rf", {}, site()).error, /未知/);
});

test("buildSystemPrompt：含規則、站上資料與開戶資訊，並標出目前標的", () => {
  const prompt = buildSystemPrompt(buildSnapshot(site()), "TBBGOLD");
  assert.ok(prompt.includes("不給買賣指令"));
  assert.ok(prompt.includes("一律呼叫工具"));
  assert.ok(prompt.includes("不要用表格"));
  assert.ok(prompt.includes("使用者目前在看的標的：台企銀黃金存摺（TBBGOLD）"));
  assert.ok(prompt.includes('"現價":4131'));
  assert.ok(prompt.includes("親洽證券分公司辦理"));
  assert.ok(!buildSystemPrompt(buildSnapshot(site()), "NOPE").includes("使用者目前在看的標的"));
});

test("runConversation：模型要求呼叫工具時，在本地執行並把結果送回", async () => {
  const calls = [];
  const callModel = async (body) => {
    calls.push(body);
    if (calls.length === 1) {
      return {
        candidates: [{ content: { role: "model", parts: [{ functionCall: { name: "calc_split_plan", args: { code: "TBBGOLD", budget: 100000 } }, thoughtSignature: "sig" }] } }],
      };
    }
    return { candidates: [{ content: { role: "model", parts: [{ text: "第一筆約 4,271 元。" }] } }] };
  };
  const reply = await runConversation({
    messages: [{ role: "user", content: "100000 元怎麼分批？" }],
    site: site(),
    focusCode: "TBBGOLD",
    callModel,
  });
  assert.equal(reply, "第一筆約 4,271 元。");
  assert.equal(calls.length, 2);
  const second = calls[1].contents;
  assert.equal(second[1].parts[0].thoughtSignature, "sig"); // 模型那一輪的內容原樣放回
  const toolResult = second[2].parts[0].functionResponse;
  assert.equal(toolResult.name, "calc_split_plan");
  assert.equal(toolResult.response.分批[0].觸發價, 4271);
  assert.equal(calls[0].tools[0].functionDeclarations.length, 2);
});

test("runConversation：沒有工具呼叫就直接回文字；空回覆與無限工具迴圈會丟錯", async () => {
  const text = async () => ({ candidates: [{ content: { parts: [{ text: "好的" }, { text: "。" }] } }] });
  assert.equal(await runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel: text }), "好的。");

  const empty = async () => ({ candidates: [{ content: { parts: [] } }] });
  await assert.rejects(runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel: empty }), /empty_reply/);

  const forever = async () => ({ candidates: [{ content: { parts: [{ functionCall: { name: "compare_costs", args: {} } }] } }] });
  await assert.rejects(
    runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel: forever }),
    /too_many_tool_rounds/
  );
});

function fakeKv(initial = {}) {
  const store = { ...initial };
  const writes = [];
  return {
    store,
    writes,
    get: async (key) => store[key] ?? null,
    put: async (key, value, opts) => {
      store[key] = value;
      writes.push({ key, value, opts });
    },
  };
}

test("checkRateLimit：每人視窗與每日總量都會擋，且每次只寫兩次 KV", async () => {
  const now = new Date("2026-11-17T03:00:00Z");
  const kv = fakeKv();
  for (let i = 0; i < LIMITS.perIpMaxRequests; i++) {
    assert.equal((await checkRateLimit(kv, "1.2.3.4", now)).ok, true);
  }
  assert.equal(kv.writes.length, LIMITS.perIpMaxRequests * 2);
  assert.deepEqual(await checkRateLimit(kv, "1.2.3.4", now), { ok: false, error: "rate_limited" });
  assert.equal((await checkRateLimit(kv, "5.6.7.8", now)).ok, true); // 別的 IP 不受影響

  const full = fakeKv({ "rl:day:2026-11-17": String(LIMITS.dailyMaxRequests) });
  assert.deepEqual(await checkRateLimit(full, "9.9.9.9", now), { ok: false, error: "daily_limit" });
  assert.deepEqual(await checkRateLimit(null, "x"), { ok: true }); // 沒有 KV 就不限制（本機測試）
});
