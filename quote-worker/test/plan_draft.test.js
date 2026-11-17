import test from "node:test";
import assert from "node:assert/strict";
import {
  PLAN_DRAFT_LIMITS,
  buildPlanDraftPrompt,
  extractPlanDraft,
  parseModelJson,
  sanitizePlanDraft,
  validatePlanDraftRequest,
} from "../src/plan_draft.js";

const chatReply = (content) => ({ choices: [{ message: { role: "assistant", content } }] });

test("validatePlanDraftRequest：要有文字、會去頭尾空白、超過上限回 message_too_long", () => {
  assert.deepEqual(validatePlanDraftRequest({ text: "  我想存教育基金  " }), { ok: true, text: "我想存教育基金" });
  assert.equal(validatePlanDraftRequest({ text: "   " }).error, "bad_request");
  assert.equal(validatePlanDraftRequest({}).error, "bad_request");
  assert.equal(validatePlanDraftRequest(null).error, "bad_request");
  assert.equal(validatePlanDraftRequest({ text: 123 }).error, "bad_request");
  assert.equal(validatePlanDraftRequest({ text: "x".repeat(PLAN_DRAFT_LIMITS.maxChars + 1) }).error, "message_too_long");
});

test("sanitizePlanDraft：選項只收清單內的值，其他鍵一律丟掉", () => {
  const draft = sanitizePlanDraft({
    purpose: "goal",
    horizon: "forever",
    code: "GCF", // 國際金價不能買賣，不在清單內
    needMoney: "maybe",
    reviewEvery: "quarter",
    advice: "現在就全部買進", // 模型多給的鍵
  });
  assert.deepEqual(draft, { purpose: "goal", needMoney: "maybe", reviewEvery: "quarter" });
});

test("sanitizePlanDraft：金額轉整數、去千分位；負數、0、過大、非數字都丟掉", () => {
  assert.deepEqual(sanitizePlanDraft({ budget: "200,000", monthly: 3000.4, extra: "20000元" }), {
    budget: 200000,
    monthly: 3000,
    extra: 20000,
  });
  assert.deepEqual(sanitizePlanDraft({ budget: -5, monthly: 0, extra: "很多" }), {});
  assert.deepEqual(sanitizePlanDraft({ budget: 1e12 }), {});
});

test("sanitizePlanDraft：可接受虧損限 0～100%，取一位小數", () => {
  assert.equal(sanitizePlanDraft({ maxLossPct: 12.345 }).maxLossPct, 12.3);
  assert.equal(sanitizePlanDraft({ maxLossPct: "10" }).maxLossPct, 10);
  assert.deepEqual(sanitizePlanDraft({ maxLossPct: 150 }), {});
  assert.deepEqual(sanitizePlanDraft({ maxLossPct: 0 }), {});
});

test("sanitizePlanDraft：文字去控制字元與角括號、截長度；字串 null 視為沒填", () => {
  const draft = sanitizePlanDraft({ purposeNote: "  <b>小孩</b>的教育基金\n ", customCondition: "null" });
  assert.equal(draft.purposeNote, "b小孩/b的教育基金");
  assert.equal("customCondition" in draft, false);
  assert.equal(sanitizePlanDraft({ purposeNote: "長".repeat(200) }).purposeNote.length, 60);
});

test("sanitizePlanDraft：不是物件就回空的", () => {
  assert.deepEqual(sanitizePlanDraft(null), {});
  assert.deepEqual(sanitizePlanDraft("budget: 100"), {});
  assert.deepEqual(sanitizePlanDraft([1, 2]), {});
});

test("parseModelJson：容忍 ```json 圍欄與前後廢話；完全不是 JSON 丟 bad_json", () => {
  assert.deepEqual(parseModelJson('```json\n{"budget":100000}\n```'), { budget: 100000 });
  assert.deepEqual(parseModelJson('好的，結果如下：{"purpose":"goal"} 以上'), { purpose: "goal" });
  assert.throws(() => parseModelJson("我不知道"), /bad_json/);
  assert.throws(() => parseModelJson("{壞掉"), /bad_json/);
  assert.throws(() => parseModelJson(undefined), /bad_json/);
});

test("extractPlanDraft：送出系統提示＋使用者原句、關閉思考、溫度 0；回傳驗證後的欄位", async () => {
  const bodies = [];
  const callModel = async (body) => {
    bodies.push(body);
    return chatReply('{"purpose":"goal","horizon":"3to5","budget":200000,"monthly":3000,"maxLossPct":10,"code":null,"purposeNote":"小孩的教育基金","extra":null}');
  };
  const result = await extractPlanDraft({ text: "五年後存教育基金，總預算 20 萬", callModel });
  assert.deepEqual(result.draft, {
    purpose: "goal",
    horizon: "3to5",
    budget: 200000,
    monthly: 3000,
    maxLossPct: 10,
    purposeNote: "小孩的教育基金",
  });
  assert.deepEqual(result.filled.sort(), ["budget", "horizon", "maxLossPct", "monthly", "purpose", "purposeNote"]);
  const body = bodies[0];
  assert.equal(body.messages[0].role, "system");
  assert.equal(body.messages[0].content, buildPlanDraftPrompt());
  assert.deepEqual(body.messages[1], { role: "user", content: "五年後存教育基金，總預算 20 萬" });
  assert.equal(body.temperature, 0);
  assert.equal(body.chat_template_kwargs.enable_thinking, false);
  assert.equal(body.tools, undefined);
});

test("extractPlanDraft：舊格式 response 也能讀；什麼都沒讀到回空的；模型亂講丟 bad_json", async () => {
  const legacy = await extractPlanDraft({ text: "x", callModel: async () => ({ response: { budget: 50000 } }) });
  assert.deepEqual(legacy.draft, { budget: 50000 });
  const empty = await extractPlanDraft({ text: "我想買黃金", callModel: async () => chatReply('{"purpose":null,"budget":null}') });
  assert.deepEqual(empty, { draft: {}, filled: [] });
  await assert.rejects(extractPlanDraft({ text: "x", callModel: async () => chatReply("抱歉") }), /bad_json/);
});

test("buildPlanDraftPrompt：要求只輸出 JSON、不給買賣建議、沒說到填 null、金額換成元", () => {
  const prompt = buildPlanDraftPrompt();
  assert.ok(prompt.includes("只輸出一個 JSON 物件"));
  assert.ok(prompt.includes("不給任何買賣建議"));
  assert.ok(prompt.includes("沒說到的欄位一律填 null"));
  assert.ok(prompt.includes("「10 萬」→100000"));
});
