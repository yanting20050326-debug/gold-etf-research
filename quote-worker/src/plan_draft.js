// 「用一句話建立購買計畫」：模型只負責把客戶的一句話轉成計畫欄位（JSON），
// 程式逐欄驗證——選項必須在清單內、金額必須是合理範圍的數字、文字有長度上限——
// 不合格的欄位直接丟掉，不會照單全收。填好的結果一定讓客戶自己確認、修改。
// 欄位名稱與網頁 localStorage 裡的購買計畫（goldCompass.plan.v1）一致。

export const PLAN_DRAFT_LIMITS = { maxChars: 300 };

export const PLAN_ENUMS = {
  purpose: ["preserve", "goal", "swing", "other"],
  horizon: ["lt1", "1to3", "3to5", "gt5"],
  code: ["TBBGOLD", "00635U", "00708L"],
  needMoney: ["no", "maybe", "yes"],
  reviewEvery: ["month", "quarter", "half"],
};

const MONEY_MAX = { budget: 100_000_000, monthly: 10_000_000, extra: 100_000_000 };
const TEXT_MAX = { purposeNote: 60, customCondition: 60 };

export function validatePlanDraftRequest(body) {
  if (!body || typeof body !== "object" || typeof body.text !== "string") return { ok: false, error: "bad_request" };
  const text = body.text.trim();
  if (!text) return { ok: false, error: "bad_request" };
  if (text.length > PLAN_DRAFT_LIMITS.maxChars) return { ok: false, error: "message_too_long" };
  return { ok: true, text };
}

export function buildPlanDraftPrompt() {
  return `你是「金羅盤」的資料整理員。使用者會用一句話描述他想怎麼買黃金，你要把內容整理成購買計畫的欄位，只輸出一個 JSON 物件，不要任何說明文字。

規則：
1. 只整理使用者「明確說到」的內容；沒說到的欄位一律填 null，不要猜、不要補建議。
2. 金額一律換成新台幣元的整數：「10 萬」→100000、「三千」→3000、「2.5 萬」→25000。
3. 不給任何買賣建議，也不要改寫使用者的意思。

欄位：
- purpose：為什麼買。preserve＝長期保值、分散資產、抗通膨；goal＝為特定目標存錢（退休、教育、買房等）；swing＝想參與金價漲跌、短線價差；other＝其他。
- horizon：預計持有多久。lt1＝1 年內；1to3＝1～3 年；3to5＝3～5 年（含 5 年）；gt5＝超過 5 年。「五年後要用」算 3to5，「十年」算 gt5。
- purposeNote：只有提到「具體的目標或用途」才填，用使用者自己的話，30 字內，例如「小孩的教育基金」「退休金」；只說保值、賺價差這類一般理由時填 null（那已經寫在 purpose）。
- code：買哪一個。黃金存摺、台企銀存摺→TBBGOLD；00635U、元大S&P黃金→00635U；00708L、正2、兩倍槓桿→00708L；只說「ETF」或沒說→null。
- budget：這份計畫總共最多投入的錢（總預算）。
- monthly：每月定期投入的金額。
- extra：回檔時才動用的額外加碼預算。
- maxLossPct：最多能接受虧損幾 %。「一成」→10、「15%」→15；說「最多虧 2 萬」且有總預算時，換算成百分比；沒說→null。
- needMoney：持有期間「提前」動用這筆錢的可能。no＝明說是閒錢、不會動用；maybe＝說可能會提前用到；yes＝說持有期間內（例如半年內）就會用到。目標到期時才要用的錢（例如五年後的教育基金）不算提前動用；沒明說就填 null。
- reviewEvery：多久回顧一次。month＝每月；quarter＝每季、三個月；half＝半年。
- customCondition：使用者說的其他行動條件，40 字內，例如「金價創新高時不追買」；已經放進其他欄位的內容（例如加碼金額已放進 extra）不要重複寫；沒有就 null。

輸出格式（鍵名固定，值可以是 null）：
{"purpose":null,"horizon":null,"purposeNote":null,"code":null,"budget":null,"monthly":null,"extra":null,"maxLossPct":null,"needMoney":null,"reviewEvery":null,"customCondition":null}`;
}

function toNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const n = Number.parseFloat(value.replace(/[,，\s]/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

// 只留下通過檢查的欄位；其餘（含模型多給的鍵）一律丟掉。
export function sanitizePlanDraft(raw) {
  const draft = {};
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return draft;
  for (const [key, allowed] of Object.entries(PLAN_ENUMS)) {
    const value = raw[key];
    if (typeof value === "string" && allowed.includes(value)) draft[key] = value;
  }
  for (const [key, max] of Object.entries(MONEY_MAX)) {
    const n = toNumber(raw[key]);
    if (n !== null && n > 0 && n <= max) draft[key] = Math.round(n);
  }
  const loss = toNumber(raw.maxLossPct);
  if (loss !== null && loss > 0 && loss <= 100) draft.maxLossPct = Math.round(loss * 10) / 10;
  for (const [key, max] of Object.entries(TEXT_MAX)) {
    if (typeof raw[key] !== "string") continue;
    const text = raw[key].replace(/[\u0000-\u001f<>]/g, "").trim();
    if (text && text.toLowerCase() !== "null") draft[key] = text.slice(0, max);
  }
  return draft;
}

// 模型偶爾會包 ```json 圍欄或在前後多講話：取第一個 { 到最後一個 } 再解析。
export function parseModelJson(content) {
  const text = String(content ?? "");
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) throw new Error("bad_json");
  try {
    return JSON.parse(text.slice(start, end + 1));
  } catch {
    throw new Error("bad_json");
  }
}

function modelContent(payload) {
  const message = payload?.choices?.[0]?.message;
  if (typeof message?.content === "string") return message.content;
  if (typeof payload?.response === "string") return payload.response;
  if (payload?.response && typeof payload.response === "object") return JSON.stringify(payload.response);
  return "";
}

// callModel(body) 由呼叫端注入（正式環境是 env.AI.run，測試是假函式）。
export async function extractPlanDraft({ text, callModel }) {
  const payload = await callModel({
    messages: [
      { role: "system", content: buildPlanDraftPrompt() },
      { role: "user", content: text },
    ],
    temperature: 0,
    max_completion_tokens: 400,
    chat_template_kwargs: { enable_thinking: false },
  });
  const draft = sanitizePlanDraft(parseModelJson(modelContent(payload)));
  return { draft, filled: Object.keys(draft) };
}
