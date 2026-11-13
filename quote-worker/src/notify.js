// 盤中即時 LINE 通知：用即時價重算「回檔階段」，碰到加碼點就廣播。
//
// 判斷規則移植自 src/gold_research/indicators.py（pullback_stage、
// trend_still_intact）與 notify_line.py，但有兩個刻意的差異：
//   1. 用「即時價」而不是最近一次收盤價，所以不用等收盤。
//   2. 通知條件改成「符合加碼條件」這個狀態的邊緣觸發（不符合 → 符合才發），
//      而不是單看階段名稱有沒有變，這樣「階段沒變，但前提剛好成立」也會通知，
//      價格在邊界來回震盪時也不會洗版。
// 回檔高點、MA20、起漲前低、止跌與 MACD 判斷都來自已發布的 gold_site.json
// （最近一次建站結果），Worker 不重算技術指標。

export const STAGE_OBSERVE = "觀察區（尚未回檔到位）";
export const STAGE_FIRST = "第一筆 20%";
export const STAGE_TREND = "加碼 20%（前提：趨勢沒壞）";
export const STAGE_STABILIZE = "加碼 20%（前提：出現止跌）";
export const STAGE_FINAL = "最後 40%（等重新轉強）";

// 需要前提條件的階段 → 對應的 stage_signals 欄位；「第一筆 20%」沒有前提。
const STAGE_SIGNAL_KEY = {
  [STAGE_TREND]: "trend_intact",
  [STAGE_STABILIZE]: "momentum_stabilizing",
  [STAGE_FINAL]: "macd_golden_cross",
};
const NOTIFIABLE_STAGES = new Set([STAGE_FIRST, ...Object.keys(STAGE_SIGNAL_KEY)]);

export function pullbackPct(recentHigh, price) {
  if (!recentHigh) return 0;
  return Math.max(0, ((recentHigh - price) / recentHigh) * 100);
}

// 門檻 3/4/6/9% × scale，與 indicators.pullback_stage 完全一致。
export function stageForPullback(pct, scale = 1) {
  if (pct < 3 * scale) return STAGE_OBSERVE;
  if (pct < 4 * scale) return STAGE_FIRST;
  if (pct < 6 * scale) return STAGE_TREND;
  if (pct < 9 * scale) return STAGE_STABILIZE;
  return STAGE_FINAL;
}

// 價格要同時在 MA20 之上且高於起漲前低；任一輸入缺資料回 null（無法判斷）。
export function liveTrendIntact(price, ma20, priorLowPrice) {
  if (ma20 == null || priorLowPrice == null) return null;
  return price > ma20 && price > priorLowPrice;
}

// 目前這個階段是否構成「可通知的加碼點」；是就回傳階段名，否則回 null。
export function actionableStage(stage, signals) {
  if (!NOTIFIABLE_STAGES.has(stage)) return null;
  const key = STAGE_SIGNAL_KEY[stage];
  if (key === undefined) return stage;
  return signals?.[key] === true ? stage : null;
}

export function buildStageMessage(name, stage, pct, price) {
  return (
    `【${name}】盤中現價 ${price.toFixed(2)}，回檔 ${pct.toFixed(1)}%，` +
    `對照紀律已達「${stage}」，可考慮依你的紀律加碼。\n` +
    "（盤中即時價判斷，收盤前可能再變動；自動通知，非投資建議，請自行判斷）"
  );
}

export function buildPriorLowBreakMessage(name, priorLow, price) {
  return (
    `【${name}】盤中現價 ${price.toFixed(2)} 已跌破起漲前低 ${priorLow.toFixed(2)}，` +
    "依你的紀律應停止加碼。\n" +
    "（盤中即時價判斷，收盤前可能回到前低之上；自動通知，非投資建議，請自行判斷）"
  );
}

// 單一標的的判斷。回傳 { state: 更新後的狀態, actions: [{ key, text, apply }] }；
// 每個 action 要「發送成功」才呼叫 apply(state) 把結果記進狀態，
// 失敗就不記，下一分鐘會自然重試。
export function evaluateTarget({ target, price, prev, today }) {
  const ind = target.indicators ?? {};
  const ps = ind.pullback_stage;
  if (!ps) return null;

  const priorLow = ind.prior_low?.price ?? null;
  const pct = pullbackPct(ps.recent_high, price);
  const stage = stageForPullback(pct, target.pullback_scale ?? 1);
  const signals = {
    trend_intact: liveTrendIntact(price, ind.ma20, priorLow),
    momentum_stabilizing: ind.stage_signals?.momentum_stabilizing ?? null,
    macd_golden_cross: ind.stage_signals?.macd_golden_cross ?? null,
  };
  const actionable = actionableStage(stage, signals);
  const isBroken = priorLow !== null && price < priorLow;

  // 沒有歷史狀態：只記錄基準，不發訊息（避免第一次啟用就誤報）。
  if (!prev) {
    return {
      state: {
        actionable,
        notified: { date: today, keys: [] },
        brokenLow: isBroken ? priorLow : null,
      },
      actions: [],
    };
  }

  const notified =
    prev.notified?.date === today ? prev.notified : { date: today, keys: [] };
  const state = {
    actionable: prev.actionable ?? null,
    notified: { date: notified.date, keys: [...notified.keys] },
    brokenLow: prev.brokenLow ?? null,
  };
  const actions = [];

  if (actionable !== state.actionable) {
    const key = `act:${actionable}`;
    if (actionable !== null && !state.notified.keys.includes(key)) {
      actions.push({
        key,
        text: buildStageMessage(target.display_name, actionable, pct, price),
        apply: (s) => {
          s.actionable = actionable;
          s.notified.keys.push(key);
        },
      });
    } else {
      // 變成「不符合」，或今天已經通知過同一個階段：只更新狀態，不發訊息。
      state.actionable = actionable;
    }
  }

  if (isBroken) {
    const key = `low:${priorLow}`;
    if (state.brokenLow !== priorLow) {
      if (!state.notified.keys.includes(key)) {
        actions.push({
          key,
          text: buildPriorLowBreakMessage(target.display_name, priorLow, price),
          apply: (s) => {
            s.brokenLow = priorLow;
            s.notified.keys.push(key);
          },
        });
      } else {
        state.brokenLow = priorLow;
      }
    }
  } else {
    state.brokenLow = null; // 回到前低之上，下次再跌破可以重新通知（每天一次上限）
  }

  return { state, actions };
}

// 讀狀態 → 逐標的判斷 → 發送 → 寫回狀態。所有外部依賴都從參數注入，方便測試。
export async function runNotifyCheck({ today, quotes, site, kv, sendLine }) {
  const stored = (await kv.get("notify_state", "json")) ?? {};
  const next = { ...stored };
  const summary = { sent: 0, failed: 0 };

  for (const [code, target] of Object.entries(site?.targets ?? {})) {
    const quote = quotes?.[code];
    if (!quote || !Number.isFinite(quote.close)) continue;
    const result = evaluateTarget({
      target,
      price: quote.close,
      prev: stored[code],
      today,
    });
    if (!result) continue;

    const { state, actions } = result;
    for (const action of actions) {
      if (await sendLine(action.text)) {
        action.apply(state);
        summary.sent += 1;
      } else {
        summary.failed += 1;
      }
    }
    next[code] = state;
  }

  if (JSON.stringify(next) !== JSON.stringify(stored)) {
    await kv.put("notify_state", JSON.stringify(next));
  }
  return summary;
}
