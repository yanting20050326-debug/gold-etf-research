import test from "node:test";
import assert from "node:assert/strict";
import {
  STAGE_FIRST,
  STAGE_FINAL,
  STAGE_OBSERVE,
  STAGE_STABILIZE,
  STAGE_TREND,
  actionableStage,
  evaluateTarget,
  liveTrendIntact,
  pullbackPct,
  runNotifyCheck,
  stageForPullback,
} from "../src/notify.js";

const TODAY = "2026-11-13";

// 以 00635U 的真實數字為底：高點 43.18、MA20 41.79、前低 40.10。
// 價格對照：43.0 = 觀察區；41.67 = 回檔 3.5%（第一筆）；41.02 = 回檔 5.0%（趨勢沒壞區）。
function makeTarget() {
  return {
    display_name: "元大S&P黃金",
    pullback_scale: 1,
    indicators: {
      ma20: 41.7885,
      pullback_stage: { recent_high: 43.18 },
      prior_low: { price: 40.1 },
      stage_signals: { momentum_stabilizing: false, macd_golden_cross: false },
    },
  };
}

function freshState(overrides = {}) {
  return { actionable: null, notified: { date: TODAY, keys: [] }, brokenLow: null, ...overrides };
}

function fakeKv(initial) {
  const store = new Map(initial ? [["notify_state", JSON.stringify(initial)]] : []);
  return {
    store,
    get: async (key) => (store.has(key) ? JSON.parse(store.get(key)) : null),
    put: async (key, value) => void store.set(key, value),
  };
}

test("stageForPullback：門檻 3/4/6/9% 與槓桿倍數", () => {
  assert.equal(stageForPullback(2.9), STAGE_OBSERVE);
  assert.equal(stageForPullback(3), STAGE_FIRST);
  assert.equal(stageForPullback(4), STAGE_TREND);
  assert.equal(stageForPullback(6), STAGE_STABILIZE);
  assert.equal(stageForPullback(9), STAGE_FINAL);
  // 2 倍槓桿：門檻等比例放大
  assert.equal(stageForPullback(7.9, 2), STAGE_FIRST);
  assert.equal(stageForPullback(8, 2), STAGE_TREND);
});

test("pullbackPct：創高時不會是負數，高點為 0 不除以零", () => {
  assert.equal(pullbackPct(43.18, 44), 0);
  assert.equal(pullbackPct(0, 10), 0);
  assert.ok(Math.abs(pullbackPct(100, 95) - 5) < 1e-9);
});

test("liveTrendIntact：缺資料回 null，否則要同時高於 MA20 與前低", () => {
  assert.equal(liveTrendIntact(42, null, 40), null);
  assert.equal(liveTrendIntact(42, 41, null), null);
  assert.equal(liveTrendIntact(42, 41, 40), true);
  assert.equal(liveTrendIntact(40.5, 41, 40), false);
  assert.equal(liveTrendIntact(39, 38, 40), false);
});

test("actionableStage：觀察區不通知、第一筆無前提、其餘看前提", () => {
  assert.equal(actionableStage(STAGE_OBSERVE, {}), null);
  assert.equal(actionableStage(STAGE_FIRST, {}), STAGE_FIRST);
  assert.equal(actionableStage(STAGE_TREND, { trend_intact: true }), STAGE_TREND);
  assert.equal(actionableStage(STAGE_TREND, { trend_intact: false }), null);
  assert.equal(actionableStage(STAGE_TREND, { trend_intact: null }), null);
  assert.equal(actionableStage(STAGE_STABILIZE, { momentum_stabilizing: true }), STAGE_STABILIZE);
  assert.equal(actionableStage(STAGE_FINAL, { macd_golden_cross: false }), null);
});

test("首次執行：只記錄基準，即使已在加碼點也不發訊息", () => {
  const r = evaluateTarget({ target: makeTarget(), price: 41.67, prev: undefined, today: TODAY });
  assert.equal(r.actions.length, 0);
  assert.equal(r.state.actionable, STAGE_FIRST);
});

test("首次執行時已經跌破前低：記為已破位，不補發", () => {
  const r = evaluateTarget({ target: makeTarget(), price: 39.9, prev: undefined, today: TODAY });
  assert.equal(r.actions.length, 0);
  assert.equal(r.state.brokenLow, 40.1);
});

test("進入第一筆區間 → 發通知，成功後才記入狀態", () => {
  const r = evaluateTarget({ target: makeTarget(), price: 41.67, prev: freshState(), today: TODAY });
  assert.equal(r.actions.length, 1);
  assert.match(r.actions[0].text, /第一筆 20%/);
  assert.equal(r.state.actionable, null); // 還沒 apply
  r.actions[0].apply(r.state);
  assert.equal(r.state.actionable, STAGE_FIRST);
  assert.deepEqual(r.state.notified.keys, [`act:${STAGE_FIRST}`]);
});

test("前提尚未成立時不通知；前提剛好成立（階段沒變）才通知", () => {
  // 回檔 5%：價格 41.02 在 MA20(41.79) 之下 → 趨勢沒壞不成立
  const below = evaluateTarget({ target: makeTarget(), price: 41.02, prev: freshState(), today: TODAY });
  assert.equal(below.actions.length, 0);

  // 同階段，但 MA20 降到 41.0 → 價格站上 MA20，前提成立
  const target = makeTarget();
  target.indicators.ma20 = 41.0;
  const above = evaluateTarget({ target, price: 41.02, prev: freshState(), today: TODAY });
  assert.equal(above.actions.length, 1);
  assert.match(above.actions[0].text, /趨勢沒壞/);
});

test("同一階段今天已通知過：價格在邊界來回只發一次", () => {
  const sent = freshState({ actionable: STAGE_FIRST, notified: { date: TODAY, keys: [`act:${STAGE_FIRST}`] } });
  // 回到觀察區 → 狀態清掉、不發訊息
  const back = evaluateTarget({ target: makeTarget(), price: 43.0, prev: sent, today: TODAY });
  assert.equal(back.actions.length, 0);
  assert.equal(back.state.actionable, null);
  // 再回第一筆區間 → 今天已通知過，不重發，但狀態更新
  const again = evaluateTarget({ target: makeTarget(), price: 41.67, prev: back.state, today: TODAY });
  assert.equal(again.actions.length, 0);
  assert.equal(again.state.actionable, STAGE_FIRST);
});

test("隔天同一階段可以再通知（每日去重只限當天）", () => {
  const yesterday = freshState({ notified: { date: "2026-11-12", keys: [`act:${STAGE_FIRST}`] } });
  const r = evaluateTarget({ target: makeTarget(), price: 41.67, prev: yesterday, today: TODAY });
  assert.equal(r.actions.length, 1);
});

test("狀態不變就不重複通知（停留在同一個加碼點）", () => {
  const prev = freshState({ actionable: STAGE_FIRST, notified: { date: TODAY, keys: [`act:${STAGE_FIRST}`] } });
  const r = evaluateTarget({ target: makeTarget(), price: 41.67, prev, today: TODAY });
  assert.equal(r.actions.length, 0);
});

test("跌破起漲前低：通知一次，回到前低之上後重置", () => {
  const r = evaluateTarget({ target: makeTarget(), price: 40.0, prev: freshState(), today: TODAY });
  const lowAction = r.actions.find((a) => a.key.startsWith("low:"));
  assert.ok(lowAction);
  assert.match(lowAction.text, /跌破起漲前低 40\.10/);
  lowAction.apply(r.state);
  assert.equal(r.state.brokenLow, 40.1);

  // 仍在前低之下 → 不重發
  const still = evaluateTarget({ target: makeTarget(), price: 39.9, prev: r.state, today: TODAY });
  assert.equal(still.actions.find((a) => a.key.startsWith("low:")), undefined);

  // 回到前低之上 → 重置
  const recovered = evaluateTarget({ target: makeTarget(), price: 41.0, prev: r.state, today: TODAY });
  assert.equal(recovered.state.brokenLow, null);
});

test("沒有 prior_low（例如 GCF）時不會誤報破位，趨勢沒壞無法判斷", () => {
  const target = makeTarget();
  target.indicators.prior_low = null;
  const r = evaluateTarget({ target, price: 41.02, prev: freshState(), today: TODAY });
  assert.equal(r.actions.length, 0);
  assert.equal(r.state.brokenLow, null);
});

test("runNotifyCheck：首次只寫基準；之後觸發才發送並寫回 KV", async () => {
  const site = { targets: { "00635U": makeTarget() } };
  const kv = fakeKv();
  const messages = [];
  const sendLine = async (text) => (messages.push(text), true);
  const run = (close) => runNotifyCheck({ today: TODAY, quotes: { "00635U": { close } }, site, kv, sendLine });

  assert.deepEqual(await run(43.0), { sent: 0, failed: 0 });
  assert.ok(kv.store.has("notify_state"));

  assert.equal((await run(41.67)).sent, 1);
  assert.equal(messages.length, 1);
  assert.equal(JSON.parse(kv.store.get("notify_state"))["00635U"].actionable, STAGE_FIRST);

  assert.equal((await run(41.67)).sent, 0);
  assert.equal(messages.length, 1);
});

test("runNotifyCheck：LINE 發送失敗時不記狀態，下一次重試", async () => {
  const site = { targets: { "00635U": makeTarget() } };
  const kv = fakeKv({ "00635U": freshState() });
  let ok = false;
  const run = () =>
    runNotifyCheck({ today: TODAY, quotes: { "00635U": { close: 41.67 } }, site, kv, sendLine: async () => ok });

  assert.deepEqual(await run(), { sent: 0, failed: 1 });
  assert.equal(JSON.parse(kv.store.get("notify_state"))["00635U"].actionable, null);

  ok = true;
  assert.deepEqual(await run(), { sent: 1, failed: 0 });
});

test("runNotifyCheck：缺報價的標的略過、沿用舊狀態", async () => {
  const old = freshState({ actionable: STAGE_FIRST });
  const site = { targets: { "00635U": makeTarget() } };
  const kv = fakeKv({ "00635U": old });
  const s = await runNotifyCheck({ today: TODAY, quotes: {}, site, kv, sendLine: async () => true });
  assert.deepEqual(s, { sent: 0, failed: 0 });
  assert.deepEqual(JSON.parse(kv.store.get("notify_state"))["00635U"], old);
});
