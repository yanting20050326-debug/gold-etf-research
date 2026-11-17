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
  parseAmount,
  parseDiscount,
  planToolRun,
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

const chatReply = (content, toolCalls) => ({
  choices: [{ message: { role: "assistant", content, ...(toolCalls ? { tool_calls: toolCalls } : {}) } }],
});
const toolCall = (name, args, id = "call_1") => ({ id, type: "function", function: { name, arguments: JSON.stringify(args) } });

test("parseAmount：萬、中文數字、帶逗號的元；沒提金額回 null", () => {
  assert.equal(parseAmount("我有 10 萬元"), 100000);
  assert.equal(parseAmount("5.5萬"), 55000);
  assert.equal(parseAmount("十萬"), 100000);
  assert.equal(parseAmount("二十五萬"), 250000);
  assert.equal(parseAmount("預算 100,000 元"), 100000);
  assert.equal(parseAmount("50000塊"), 50000);
  assert.equal(parseAmount("怎麼分批"), null);
  assert.equal(parseAmount("買 5 公克"), null);
});

test("parseDiscount：6 折、6.5 折、65 折；沒提或不合理回 null", () => {
  assert.equal(parseDiscount("手續費 6 折"), 0.6);
  assert.equal(parseDiscount("6.5折"), 0.65);
  assert.equal(parseDiscount("65 折"), 0.65);
  assert.equal(parseDiscount("沒折扣"), null);
  assert.equal(parseDiscount("0 折"), null);
});

test("planToolRun：分批題由程式解析預算並預先試算（數字來自工具，不是模型）", () => {
  const run = planToolRun([{ role: "user", content: "我有 10 萬元，要買台企銀黃金存摺，怎麼分批？" }], "TBBGOLD", site());
  assert.equal(run.name, "calc_split_plan");
  assert.deepEqual(run.args, { code: "TBBGOLD", budget: 100000 });
  assert.equal(run.result.分批[0].觸發價, 4271);
  assert.equal(run.result.分批.length, 4);
});

test("planToolRun：標的由題目決定，其次是目前在看的標的", () => {
  const ask = (text, focus) => planToolRun([{ role: "user", content: text }], focus, site()).args.code;
  assert.equal(ask("5 萬買 00708L 怎麼分批", "TBBGOLD"), "00708L");
  assert.equal(ask("5 萬怎麼分批", "00635U"), "00635U");
  assert.equal(ask("5 萬怎麼分批", "GCF"), "TBBGOLD"); // 國際金價不能買賣，改用存摺
});

test("planToolRun：沒有預算不試算（讓模型去問）；追問『那 5 萬呢』會沿用上一題的意圖範圍", () => {
  assert.equal(planToolRun([{ role: "user", content: "我想分批買，怎麼分？" }], "TBBGOLD", site()), null);
  const followUp = planToolRun(
    [
      { role: "user", content: "10 萬怎麼分批？" },
      { role: "assistant", content: "…" },
      { role: "user", content: "那 5 萬呢？" },
    ],
    "TBBGOLD",
    site()
  );
  assert.equal(followUp.args.budget, 50000);
});

test("planToolRun：成本比較解析金額與折數，沒提就用工具預設", () => {
  const withArgs = planToolRun([{ role: "user", content: "存摺和 ETF 哪個成本低？用 20 萬算，手續費 6 折" }], "TBBGOLD", site());
  assert.equal(withArgs.name, "compare_costs");
  assert.deepEqual(withArgs.args, { amount: 200000, broker_fee_discount: 0.6 });
  assert.equal(withArgs.result.台企銀黃金存摺.進出一趟成本_金額, 2227); // 假資料牌價 4131／4085，價差 1.1135%
  assert.equal(withArgs.result.黃金ETF.進出一趟成本_金額, 542);

  const bare = planToolRun([{ role: "user", content: "存摺還是 ETF 比較划算？" }], "TBBGOLD", site());
  assert.deepEqual(bare.args, {});
  assert.equal(bare.result.投入金額, 100000);
});

test("planToolRun：一般問題不預先試算", () => {
  assert.equal(planToolRun([{ role: "user", content: "RSI 是什麼？" }], "TBBGOLD", site()), null);
  assert.equal(planToolRun([{ role: "user", content: "怎麼申辦黃金存摺？" }], "TBBGOLD", site()), null);
});

test("runConversation：預先試算的結果放進提示詞，且不再給模型工具（不會被它改數字）", async () => {
  const bodies = [];
  const callModel = async (body) => {
    bodies.push(body);
    return chatReply("第一筆 4271 元。");
  };
  const reply = await runConversation({
    messages: [{ role: "user", content: "10 萬怎麼分批？" }],
    site: site(),
    focusCode: "TBBGOLD",
    callModel,
  });
  assert.equal(reply, "第一筆 4271 元。");
  assert.equal(bodies.length, 1);
  assert.equal(bodies[0].tools, undefined);
  assert.equal(bodies[0].chat_template_kwargs.enable_thinking, false); // 推理模型要關思考，否則 token 全花在思考上
  const [system, user] = bodies[0].messages;
  assert.equal(system.role, "system");
  assert.ok(system.content.includes("【本題試算結果】"));
  assert.ok(system.content.includes('"觸發價":4271'));
  assert.deepEqual(user, { role: "user", content: "10 萬怎麼分批？" });
});

test("runConversation：一般問題給工具；模型要求呼叫時在本地執行並把結果送回", async () => {
  const bodies = [];
  const callModel = async (body) => {
    bodies.push(JSON.parse(JSON.stringify(body)));
    if (bodies.length === 1) return chatReply("", [toolCall("calc_split_plan", { code: "TBBGOLD", budget: 100000 })]);
    return chatReply("第一筆約 4,271 元。");
  };
  const reply = await runConversation({
    messages: [{ role: "user", content: "如果照紀律，我手上資金該怎麼用？" }],
    site: site(),
    focusCode: "TBBGOLD",
    callModel,
  });
  assert.equal(reply, "第一筆約 4,271 元。");
  assert.equal(bodies.length, 2);
  assert.equal(bodies[0].tools.length, 2);
  assert.equal(bodies[0].tools[0].type, "function");
  assert.equal(bodies[0].tool_choice, "auto");
  const second = bodies[1].messages;
  const assistantTurn = second[second.length - 2];
  assert.equal(assistantTurn.role, "assistant");
  assert.equal(assistantTurn.tool_calls[0].function.name, "calc_split_plan");
  const toolTurn = second[second.length - 1];
  assert.equal(toolTurn.role, "tool");
  assert.equal(toolTurn.tool_call_id, "call_1");
  assert.equal(JSON.parse(toolTurn.content).分批[0].觸發價, 4271);
});

test("runConversation：工具參數壞掉或是物件都能處理，未知工具回錯誤給模型", async () => {
  const bodies = [];
  const callModel = async (body) => {
    bodies.push(JSON.parse(JSON.stringify(body)));
    if (bodies.length === 1) {
      return chatReply("", [
        { id: "a", type: "function", function: { name: "compare_costs", arguments: "{壞掉" } },
        { id: "b", type: "function", function: { name: "compare_costs", arguments: { amount: 50000 } } },
        { id: "c", type: "function", function: { name: "rm_rf", arguments: "{}" } },
      ]);
    }
    return chatReply("好。");
  };
  assert.equal(await runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel }), "好。");
  const tools = bodies[1].messages.filter((m) => m.role === "tool").map((m) => JSON.parse(m.content));
  assert.equal(tools[0].投入金額, 100000); // 壞掉的參數當成沒填，用預設
  assert.equal(tools[1].投入金額, 50000);
  assert.match(tools[2].error, /未知/);
});

test("runConversation：沒有工具呼叫就直接回文字；空回覆與無限工具迴圈會丟錯", async () => {
  const text = async () => chatReply("好的。");
  assert.equal(await runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel: text }), "好的。");

  const parts = async () => chatReply([{ type: "text", text: "好的" }, { type: "text", text: "。" }]);
  assert.equal(await runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel: parts }), "好的。");

  const legacy = async () => ({ response: "舊格式也行。" });
  assert.equal(await runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel: legacy }), "舊格式也行。");

  const empty = async () => chatReply("");
  await assert.rejects(runConversation({ messages: [{ role: "user", content: "hi" }], site: site(), callModel: empty }), /empty_reply/);

  const forever = async () => chatReply("", [toolCall("compare_costs", {})]);
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
