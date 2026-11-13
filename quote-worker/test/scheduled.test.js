import test from "node:test";
import assert from "node:assert/strict";
import { formatTaipeiDate } from "../src/quotes.js";
import { handleScheduled } from "../src/index.js";

test("formatTaipeiDate：以台北日曆日為準（UTC 16:00 後已是隔天）", () => {
  assert.equal(formatTaipeiDate(new Date("2026-11-13T03:00:00Z")), "2026-11-13");
  assert.equal(formatTaipeiDate(new Date("2026-11-13T15:59:00Z")), "2026-11-13");
  assert.equal(formatTaipeiDate(new Date("2026-11-13T16:00:00Z")), "2026-11-14");
});

// 這些情況都必須在碰任何網路之前就返回；若真的發出請求，測試會因 fetch 失敗而拋錯。
function noNetwork(fn) {
  return async () => {
    const original = globalThis.fetch;
    globalThis.fetch = () => {
      throw new Error("unexpected network call");
    };
    try {
      await fn();
    } finally {
      globalThis.fetch = original;
    }
  };
}

test(
  "handleScheduled：沒設定 token 就略過",
  noNetwork(async () => {
    const kv = { get: async () => null, put: async () => {} };
    await handleScheduled({ scheduledTime: Date.parse("2026-11-13T03:00:00Z") }, { NOTIFY_STATE: kv });
  }),
);

test(
  "handleScheduled：沒綁定 KV 就略過",
  noNetwork(async () => {
    await handleScheduled(
      { scheduledTime: Date.parse("2026-11-13T03:00:00Z") },
      { LINE_CHANNEL_ACCESS_TOKEN: "x" },
    );
  }),
);

test(
  "handleScheduled：非台股盤中且不是 5 的倍數分鐘就略過",
  noNetwork(async () => {
    const kv = { get: async () => null, put: async () => {} };
    // UTC 10:03 = 台北 18:03，盤後，分鐘 3 不是 5 的倍數
    await handleScheduled(
      { scheduledTime: Date.parse("2026-11-13T10:03:00Z") },
      { LINE_CHANNEL_ACCESS_TOKEN: "x", NOTIFY_STATE: kv },
    );
  }),
);
