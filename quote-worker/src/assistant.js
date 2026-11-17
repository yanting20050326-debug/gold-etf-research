// AI 小幫手的核心邏輯（純函式，不碰網路，方便單元測試）。
//
// 設計原則：
// - 「算數」交給這裡的函式，不交給語言模型：分批計畫、成本比較都是確定性的計算，
//   模型只負責用白話解釋結果，避免它自己心算出錯。
// - 只根據站上已發布的資料與下面寫死的、已查證的開戶規則回答，不知道就說不知道。
// - 不給買賣指令；可以說明「依紀律目前落在哪一階段」。

export const ALLOWED_ORIGINS = [
  "https://yanting20050326-debug.github.io",
  "http://localhost:4321",
  "http://127.0.0.1:4321",
];

export const LIMITS = {
  maxMessages: 8,
  maxMessageChars: 500, // 使用者每則
  maxAssistantChars: 1500, // 小幫手自己的回覆（分批計畫會比較長），下一輪會被帶回來
  maxTotalChars: 5000,
  perIpWindowSeconds: 600,
  perIpMaxRequests: 15,
  dailyMaxRequests: 300,
  maxToolRounds: 3,
};

// 回檔門檻（％）與各階段投入比例，跟網站的紀律一致（槓桿標的門檻乘上 pullback_scale）。
const PULLBACK_STEPS = [
  { stage: "第一筆", thresholdPct: 3, allocationPct: 20, condition: "回檔到位即可" },
  { stage: "加碼一", thresholdPct: 4, allocationPct: 20, condition: "前提：趨勢沒壞（現價在月均線上且未破前低）" },
  { stage: "加碼二", thresholdPct: 6, allocationPct: 20, condition: "前提：出現止跌（力道回升且沒再創新低）" },
  { stage: "最後一批", thresholdPct: 9, allocationPct: 40, condition: "前提：重新轉強（MACD 黃金交叉），不要無限攤平" },
];
const TAKE_PROFIT_PCTS = [5, 8, 11];

const round = (value, digits = 2) => {
  const f = 10 ** digits;
  return Math.round(value * f) / f;
};

// ---- 請求驗證 -----------------------------------------------------------

export function isAllowedOrigin(origin) {
  return typeof origin === "string" && ALLOWED_ORIGINS.includes(origin);
}

// 回傳 { ok:true, messages, code } 或 { ok:false, error }。
export function validateChatRequest(body) {
  if (!body || typeof body !== "object") return { ok: false, error: "bad_request" };
  const { messages, code } = body;
  if (!Array.isArray(messages) || messages.length === 0) return { ok: false, error: "bad_request" };
  if (messages.length > LIMITS.maxMessages) return { ok: false, error: "too_many_messages" };
  let total = 0;
  const cleaned = [];
  for (const m of messages) {
    if (!m || (m.role !== "user" && m.role !== "assistant") || typeof m.content !== "string") {
      return { ok: false, error: "bad_request" };
    }
    const content = m.content.trim();
    if (!content) return { ok: false, error: "bad_request" };
    const maxChars = m.role === "assistant" ? LIMITS.maxAssistantChars : LIMITS.maxMessageChars;
    if (content.length > maxChars) return { ok: false, error: "message_too_long" };
    total += content.length;
    cleaned.push({ role: m.role, content });
  }
  if (total > LIMITS.maxTotalChars) return { ok: false, error: "message_too_long" };
  if (cleaned[cleaned.length - 1].role !== "user") return { ok: false, error: "bad_request" };
  return { ok: true, messages: cleaned, code: typeof code === "string" ? code : null };
}

// ---- 站上資料摘要（給模型當背景，也給工具計算用）--------------------------

function changePct(target) {
  const ref = target.latest?.prev_close;
  const price = target.latest?.close;
  if (!ref || price === null || price === undefined) return null;
  return round(((price - ref) / ref) * 100, 2);
}

const orNull = (value, digits) => (value === null || value === undefined ? null : round(value, digits));

export function summarizeTarget(target) {
  const ind = target.indicators ?? {};
  const ps = ind.pullback_stage;
  const sig = ind.stage_signals ?? {};
  return {
    代碼: target.code,
    名稱: target.display_name,
    單位: target.unit ?? (target.code === "GCF" ? "美元／盎司" : "元"),
    現價: target.latest?.close ?? null,
    今日漲跌幅_百分比: changePct(target),
    價格類型: target.passbook_quote ? "銀行牌價（本行賣出價）" : target.latest?.is_realtime ? "盤中即時價" : "最近收盤價",
    台企銀牌價: target.passbook_quote
      ? { 賣出價: target.passbook_quote.sell, 買進價: target.passbook_quote.buy, 價差: target.passbook_quote.spread, 掛牌時間: target.passbook_quote.listed_at }
      : undefined,
    槓桿倍數: target.pullback_scale ?? 1,
    回檔階段: ps ? ps.stage : null,
    從30日高點回檔_百分比: ps ? round(ps.pullback_pct, 2) : null,
    三十日高點: ps ? ps.recent_high : null,
    階段提示: ps ? ps.hint : null,
    起漲前低: ind.prior_low ? { 價格: ind.prior_low.price, 幾天前: ind.prior_low.days_ago } : ind.prior_low_broken ? "已跌破所有前低（趨勢轉弱）" : "資料不足",
    趨勢沒壞: sig.trend_intact ?? null,
    已止跌: sig.momentum_stabilizing ?? null,
    MACD黃金交叉: sig.macd_golden_cross ?? null,
    RSI14: orNull(ind.rsi14, 1),
    MA20: orNull(ind.ma20, 2),
    布林通道: ind.bollinger ? { 下緣: orNull(ind.bollinger.lower, 2), 上緣: orNull(ind.bollinger.upper, 2) } : null,
    MACD: ind.macd ? { macd: orNull(ind.macd.macd, 3), 訊號線: orNull(ind.macd.signal, 3) } : null,
    區間位置_30天百分比: ind.relative_position ? round(ind.relative_position.position * 100, 0) : null,
    波動壓縮比: orNull(ind.volatility_squeeze?.ratio, 2),
    綜合冷熱分數: ind.technical_score
      ? { 分數: round(ind.technical_score.composite, 0), 結論: ind.technical_score.label, 說明: "分數越高越偏冷（價格在低檔），越低越偏熱" }
      : null,
    今日AI推測: target.ai_summary?.text ?? null,
  };
}

export function buildSnapshot(site) {
  const targets = {};
  for (const [code, target] of Object.entries(site?.targets ?? {})) {
    targets[code] = summarizeTarget(target);
  }
  return { 資料產生時間: site?.generated_at ?? null, 標的: targets };
}

// ---- 工具：分批計畫 ------------------------------------------------------

export function calcSplitPlan(site, args) {
  const code = args?.code;
  const budget = Number(args?.budget);
  const target = site?.targets?.[code];
  if (!target) return { error: `找不到標的 ${code}` };
  if (code === "GCF") return { error: "國際金價（COMEX 期貨）無法在台灣直接買賣，請改選台企銀黃金存摺、00635U 或 00708L。" };
  if (!Number.isFinite(budget) || budget <= 0) return { error: "請提供大於 0 的總預算（新台幣）。" };

  const ps = target.indicators?.pullback_stage;
  if (!ps) return { error: "這個標的的資料不足，無法計算回檔價位。" };
  const scale = target.pullback_scale ?? 1;
  const price = target.latest.close;
  const unit = target.code === "TBBGOLD" ? "公克" : "股";
  const digits = target.price_digits ?? 2;

  const steps = PULLBACK_STEPS.map((step) => {
    const thresholdPct = step.thresholdPct * scale;
    const triggerPrice = round(ps.recent_high * (1 - thresholdPct / 100), digits);
    const amount = round((budget * step.allocationPct) / 100, 0);
    return {
      階段: step.stage,
      回檔門檻_百分比: thresholdPct,
      觸發價: triggerPrice,
      投入比例_百分比: step.allocationPct,
      投入金額: amount,
      約可買數量: Math.floor(amount / triggerPrice),
      數量單位: unit,
      條件: step.condition,
      目前狀態: ps.pullback_pct >= thresholdPct ? "回檔幅度已到" : "尚未到",
    };
  });

  const totalUnits = steps.reduce((sum, s) => sum + s.約可買數量, 0);
  const spent = steps.reduce((sum, s) => sum + s.約可買數量 * s.觸發價, 0);
  const avgCost = totalUnits > 0 ? round(spent / totalUnits, digits) : null;
  return {
    標的: target.display_name,
    代碼: code,
    目前價格: price,
    三十日高點: ps.recent_high,
    目前回檔_百分比: round(ps.pullback_pct, 2),
    目前階段: ps.stage,
    總預算: budget,
    分批: steps,
    假設四批都在觸發價成交: avgCost === null ? null : { 平均成本: avgCost, 合計數量: totalUnits, 數量單位: unit },
    分批獲利參考:
      avgCost === null
        ? null
        : TAKE_PROFIT_PCTS.map((pct) => ({ 漲幅_百分比: pct === 11 ? "10～12" : pct, 參考價: round(avgCost * (1 + pct / 100), digits) })),
    提醒: "跌破起漲前低就停止加碼；這是依站上紀律的試算，不是買賣建議。實際成交價、手續費與價差會讓結果不同。",
  };
}

// ---- 工具：存摺 vs ETF 成本比較 ------------------------------------------

export function compareCosts(site, args) {
  const amount = args?.amount === undefined || args?.amount === null ? 100000 : Number(args.amount);
  const discount = args?.broker_fee_discount === undefined || args?.broker_fee_discount === null ? 1 : Number(args.broker_fee_discount);
  if (!Number.isFinite(amount) || amount <= 0) return { error: "請提供大於 0 的金額（新台幣）。" };
  if (!Number.isFinite(discount) || discount <= 0 || discount > 1) return { error: "券商手續費折扣請用 0 到 1 之間的數字，例如 6 折填 0.6。" };

  const quote = site?.targets?.TBBGOLD?.passbook_quote;
  if (!quote) return { error: "目前沒有台企銀牌價資料，無法比較。" };

  const spreadPct = ((quote.sell - quote.buy) / quote.sell) * 100;
  const brokerFeePct = 0.1425 * discount; // 單邊
  const taxPct = 0.1;
  const etfRoundTripPct = brokerFeePct * 2 + taxPct;
  return {
    投入金額: amount,
    台企銀黃金存摺: {
      牌價: { 賣出價: quote.sell, 買進價: quote.buy, 掛牌時間: quote.listed_at },
      進出一趟成本_百分比: round(spreadPct, 2),
      進出一趟成本_金額: round((amount * spreadPct) / 100, 0),
      開戶費: "網銀 50 元、臨櫃 100 元（台企銀官網公告）",
      說明: "成本主要是買賣價差；買進用賣出價、賣回用買進價。黃金存摺不是存款、沒有利息、不受存款保險保障。",
    },
    黃金ETF: {
      進出一趟成本_百分比: round(etfRoundTripPct, 3),
      進出一趟成本_金額: round((amount * etfRoundTripPct) / 100, 0),
      計算假設: `券商手續費 0.1425%×${discount} 買賣各一次，加賣出時證交稅 0.1%（一般規定，實際以券商公告為準）`,
      未計入: "ETF 的經理費與保管費（已內含在淨值）、買賣價差、期貨轉倉成本；00708L 是 2 倍槓桿，每日重設，長期持有會偏離原型走勢。",
    },
    結論提示: "短進短出看『進出一趟成本』；長期持有還要看 ETF 內含費用與轉倉成本，這裡沒有納入。",
  };
}

// ---- 工具宣告（給 Gemini function calling）-------------------------------

export const TOOL_DECLARATIONS = [
  {
    name: "calc_split_plan",
    description: "依站上的分批紀律，替指定標的與總預算試算各階段的觸發價、投入金額、約可買數量、平均成本與分批獲利參考價。使用者問分批計畫、要買多少、什麼價位時呼叫。",
    parameters: {
      type: "object",
      properties: {
        code: { type: "string", description: "標的代碼：TBBGOLD（台企銀黃金存摺）、00635U、00708L。國際金價 GCF 無法直接買賣。" },
        budget: { type: "number", description: "總預算，新台幣元。" },
      },
      required: ["code", "budget"],
    },
  },
  {
    name: "compare_costs",
    description: "比較台企銀黃金存摺與黃金 ETF 進出一趟的交易成本。使用者問存摺和 ETF 哪個划算、成本差多少時呼叫。",
    parameters: {
      type: "object",
      properties: {
        amount: { type: "number", description: "投入金額，新台幣元；沒指定就不要填。" },
        broker_fee_discount: { type: "number", description: "券商手續費折扣，0 到 1，例如 6 折填 0.6；沒指定就不要填。" },
      },
    },
  },
];

export function runTool(name, args, site) {
  if (name === "calc_split_plan") return calcSplitPlan(site, args);
  if (name === "compare_costs") return compareCosts(site, args);
  return { error: `未知的工具 ${name}` };
}

// ---- 提示詞 --------------------------------------------------------------

// 已向台企銀官網查證的開戶資訊（2026-11-17）；改版後需重新確認。
const ONBOARDING_FACTS = `【新手開戶資訊（來自臺灣企銀官網，以銀行公告為準）】
- 黃金存摺：可臨櫃，或用網路銀行線上開戶（需年滿 18 歲本國人、已申請網銀並約定新臺幣活存轉出帳號、已申請晶片金融卡或電子簽章）。開戶費：臨櫃 100 元、網銀 50 元。1 公克即可買賣，每次須為 1 公克的整倍數；有定期定額（每月 6、16、26 日擇日，每次至少 3,000 元）。黃金不是存款、不計利息、不受存款保險保障，價格有漲有跌，最大可能損失為買進金額全部。牌價一天會更新多次。
- 買黃金 ETF（00635U、00708L）：需要證券帳戶。臺灣企銀證券開戶須攜身分證、印鑑，親洽證券分公司辦理（沒有線上開戶）；也可以用其他券商。
- 00635U：追蹤 COMEX 黃金期貨，無槓桿，期貨轉倉遇正價差有小幅成本。00708L：2 倍槓桿，每日重設，僅適合短線，長期持有會偏離。
- 黃金存摺是銀行牌價（買賣有價差）；ETF 是盤中交易所價格（有手續費與證交稅）。`;

export function buildSystemPrompt(snapshot, focusCode) {
  const focusTarget = focusCode ? snapshot.標的[focusCode] : null;
  const focus = focusTarget ? `使用者目前在看的標的：${focusTarget.名稱}（${focusCode}）。\n` : "";
  return `你是「金羅盤」網站的 AI 小幫手，用繁體中文、白話、簡短地回答關於黃金（國際金價、台企銀黃金存摺、黃金 ETF）的問題。

規則：
1. 只根據下面【站上資料】、工具計算結果與【新手開戶資訊】回答；沒有的資訊就說不知道，不要編造，也不要假裝知道新聞或事件。
2. 不給買賣指令，不說「你該買」「你該賣」。可以說明「依站上的分批紀律，目前落在哪一階段」「這個條件是否符合」，並提醒這是研究參考。
3. 要算數（分批計畫、成本比較）一律呼叫工具，不要自己心算；工具回傳什麼就說什麼，並講清楚假設。
4. 解釋指標時先講它代表什麼，再講目前數字落在哪一區、意味著什麼；數字大小的意義要說清楚。
5. 回答控制在 200 字內，除非是分批計畫表；先給結論再補充。使用者問到站外或無關的話題，禮貌拉回黃金與本站功能。
6. 國際金價（COMEX）無法在台灣直接買賣，要提醒使用者改看存摺或 ETF。
7. 站上的所有價格僅供參考：台企銀牌價的掛牌時間見資料；國際金價約延遲 10 分鐘。
8. 輸出純文字：用條列（每行一點）呈現，不要用表格、標題或 markdown 符號（例如 ** 或 #）。

${focus}【站上資料】
${JSON.stringify(snapshot)}

${ONBOARDING_FACTS}`;
}

// ---- 速率限制 --------------------------------------------------------------

// KV 免費額度每天 1,000 次寫入，所以每次聊天只寫兩次（IP 視窗 + 當日總量），並設全站每日上限。
export async function checkRateLimit(kv, ip, now = new Date()) {
  if (!kv) return { ok: true };
  const windowKey = `rl:ip:${ip}:${Math.floor(now.getTime() / 1000 / LIMITS.perIpWindowSeconds)}`;
  const dayKey = `rl:day:${now.toISOString().slice(0, 10)}`;
  const [ipCount, dayCount] = await Promise.all([kv.get(windowKey), kv.get(dayKey)]);
  if (Number(dayCount ?? 0) >= LIMITS.dailyMaxRequests) return { ok: false, error: "daily_limit" };
  if (Number(ipCount ?? 0) >= LIMITS.perIpMaxRequests) return { ok: false, error: "rate_limited" };
  await Promise.all([
    kv.put(windowKey, String(Number(ipCount ?? 0) + 1), { expirationTtl: LIMITS.perIpWindowSeconds + 60 }),
    kv.put(dayKey, String(Number(dayCount ?? 0) + 1), { expirationTtl: 60 * 60 * 36 }),
  ]);
  return { ok: true };
}

// ---- 與 Gemini 的對話迴圈 ---------------------------------------------------

export const GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent";

function toGeminiContents(messages) {
  return messages.map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: [{ text: m.content }] }));
}

// 呼叫 Gemini，遇到 functionCall 就在本地執行工具再把結果送回去，最多 maxToolRounds 輪。
// callModel(body) 由呼叫端注入（正式環境是 fetch，測試是假函式）。
export async function runConversation({ messages, site, focusCode, callModel }) {
  const snapshot = buildSnapshot(site);
  const contents = toGeminiContents(messages);
  const baseBody = {
    systemInstruction: { parts: [{ text: buildSystemPrompt(snapshot, focusCode) }] },
    tools: [{ functionDeclarations: TOOL_DECLARATIONS }],
    generationConfig: { temperature: 0.3, maxOutputTokens: 900 },
  };

  for (let toolRound = 0; toolRound <= LIMITS.maxToolRounds; toolRound++) {
    const payload = await callModel({ ...baseBody, contents });
    const content = payload?.candidates?.[0]?.content;
    const parts = content?.parts ?? [];
    const calls = parts.filter((p) => p.functionCall);
    if (calls.length === 0) {
      const text = parts.map((p) => p.text ?? "").join("").trim();
      if (!text) throw new Error("empty_reply");
      return text;
    }
    if (toolRound === LIMITS.maxToolRounds) throw new Error("too_many_tool_rounds");
    // 原樣放回模型這一輪的內容（含可能的 thoughtSignature），再附上工具結果。
    contents.push(content);
    contents.push({
      role: "user",
      parts: calls.map((p) => ({
        functionResponse: { name: p.functionCall.name, response: runTool(p.functionCall.name, p.functionCall.args ?? {}, site) },
      })),
    });
  }
  throw new Error("too_many_tool_rounds");
}
