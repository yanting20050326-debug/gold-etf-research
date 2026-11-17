import test from "node:test";
import assert from "node:assert/strict";
import { loadPayload, resetTwseMemory } from "../src/index.js";

// 2026-11-17 是星期二；02:00Z = 台北 10:00，在台股盤中。
const NOW = new Date("2026-11-17T02:00:00Z");
const noSleep = async () => {};

const twseBody = (price) => ({
  rtmessage: "OK",
  msgArray: [
    { c: "00635U", z: String(price), y: "39.74", t: "10:00:00", a: "-", b: "-" },
    { c: "00708L", z: "61.55", y: "61.25", t: "10:00:00", a: "-", b: "-" },
  ],
});
const yahooBody = {
  chart: { result: [{ timestamp: [1794880000], indicators: { quote: [{ close: [3883.2] }] } }] },
};

// 假 fetch：TWSE 依序吃 twseResponses（Response 或要丟的 Error），Yahoo 固定成功。
async function withFetch(twseResponses, fn) {
  const original = globalThis.fetch;
  const twseCalls = [];
  globalThis.fetch = async (url, init) => {
    if (String(url).includes("twse.com.tw")) {
      twseCalls.push({ url: String(url), init });
      const next = twseResponses.shift();
      if (next instanceof Error) throw next;
      return next ?? new Response("exhausted", { status: 500 });
    }
    return new Response(JSON.stringify(yahooBody), { status: 200 });
  };
  try {
    return await fn(twseCalls);
  } finally {
    globalThis.fetch = original;
  }
}

const ok = (price = 39.8) => new Response(JSON.stringify(twseBody(price)), { status: 200 });
const fail = (status = 520) => new Response("origin error", { status });

function fakeKv() {
  const store = new Map();
  const puts = [];
  return {
    puts,
    store,
    get: async (key, type) => {
      const value = store.get(key);
      return value === undefined ? null : type === "json" ? JSON.parse(value) : value;
    },
    put: async (key, value, opts) => {
      puts.push({ key, opts });
      store.set(key, value);
    },
  };
}

test("TWSE 第一次 520、重試成功：照常回報價，標頭帶 Referer", async () => {
  resetTwseMemory();
  await withFetch([fail(520), ok(39.8)], async (calls) => {
    const payload = await loadPayload(NOW, { sleep: noSleep });
    assert.equal(payload.quotes["00635U"].close, 39.8);
    assert.equal(calls.length, 2);
    assert.match(calls[0].init.headers.Referer, /mis\.twse\.com\.tw/);
  });
});

test("TWSE 連續失敗：沿用同一天稍早的好價，並保留原本的成交時間", async () => {
  resetTwseMemory();
  await withFetch([ok(39.8)], async () => {
    await loadPayload(NOW, { sleep: noSleep });
  });
  const later = new Date(NOW.getTime() + 10 * 60 * 1000);
  const logged = [];
  const original = console.error;
  console.error = (...args) => logged.push(args.join(" "));
  try {
    await withFetch([fail(520), fail(520), fail(502)], async (calls) => {
      const payload = await loadPayload(later, { sleep: noSleep });
      assert.equal(calls.length, 3); // 試滿三次才放棄
      assert.equal(payload.quotes["00635U"].close, 39.8);
      assert.equal(payload.quotes["00635U"].quote_time, "10:00:00");
      assert.equal(payload.quotes.GCF.close, 3883.2); // 國際金價不受影響
    });
  } finally {
    console.error = original;
  }
  assert.ok(logged.some((line) => line.includes("serving last good quotes")));
});

test("超過 2 小時的舊價、或不是今天的價格，不拿來頂替", async () => {
  resetTwseMemory();
  await withFetch([ok(39.8)], async () => {
    await loadPayload(NOW, { sleep: noSleep });
  });

  const threeHoursLater = new Date(NOW.getTime() + 3 * 60 * 60 * 1000); // 台北 13:00，仍在盤中
  await withFetch([fail(), fail(), fail()], async () => {
    const payload = await loadPayload(threeHoursLater, { sleep: noSleep });
    assert.equal(payload.quotes["00635U"], undefined);
    assert.ok(payload.quotes.GCF);
  });

  const nextDay = new Date("2026-11-18T02:00:00Z");
  await withFetch([fail(), fail(), fail()], async () => {
    const payload = await loadPayload(nextDay, { sleep: noSleep });
    assert.equal(payload.quotes["00635U"], undefined);
  });
});

test("沒有任何好價可沿用時，維持原行為：沒有 ETF 報價、國際金價照常", async () => {
  resetTwseMemory();
  const logged = [];
  const original = console.error;
  console.error = (...args) => logged.push(args.join(" "));
  try {
    await withFetch([fail(), fail(), fail()], async () => {
      const payload = await loadPayload(NOW, { sleep: noSleep });
      assert.equal(payload.quotes["00635U"], undefined);
      assert.equal(payload.quotes.GCF.close, 3883.2);
    });
  } finally {
    console.error = original;
  }
  assert.ok(logged.some((line) => line.includes("TWSE failed")));
});

test("好價會存進 KV（最多每 5 分鐘一次），換了 isolate 也能從 KV 拿回來", async () => {
  resetTwseMemory();
  const kv = fakeKv();
  await withFetch([ok(39.8), ok(39.9)], async () => {
    await loadPayload(NOW, { kv, sleep: noSleep });
    await loadPayload(new Date(NOW.getTime() + 60 * 1000), { kv, sleep: noSleep });
  });
  assert.equal(kv.puts.length, 1); // 第二次在 5 分鐘內，不再寫 KV（保護免費寫入額度）
  assert.equal(kv.puts[0].key, "twse:last_good");
  assert.equal(kv.puts[0].opts.expirationTtl, 7200);

  resetTwseMemory(); // 模擬換了一個全新的 isolate
  await withFetch([fail(), fail(), fail()], async () => {
    const payload = await loadPayload(new Date(NOW.getTime() + 2 * 60 * 1000), { kv, sleep: noSleep });
    assert.equal(payload.quotes["00635U"].close, 39.8);
  });
});

test("盤外時間不打 TWSE", async () => {
  resetTwseMemory();
  const night = new Date("2026-11-17T14:00:00Z"); // 台北 22:00
  await withFetch([], async (calls) => {
    const payload = await loadPayload(night, { sleep: noSleep });
    assert.equal(calls.length, 0);
    assert.equal(payload.quotes["00635U"], undefined);
  });
});
