// 定期呼叫 GitHub API 觸發 gold-research-site.yml 這個 workflow_dispatch。
//
// GitHub Actions 自己的 `schedule:` 排程是「盡力而為」，實測常常延遲一到
// 三小時才真的跑一次，跟設定的每 5 分鐘完全對不上。Cloudflare Cron
// Triggers 是正式的排程功能，穩定性高很多，所以改用這個 Worker 定期外部
// 觸發，繞開 GitHub 自己排程系統不可靠的問題。
//
// 需要一組 fine-grained GitHub PAT，只給 Ting-Agent 這個 repo 的
// "Actions: Read and write" 權限（不需要別的），存成這個 Worker 的
// secret，名稱 GITHUB_PAT（用 `wrangler secret put GITHUB_PAT` 設定）。

const DISPATCH_URL =
  "https://api.github.com/repos/yanting20050326-debug/Ting-Agent/actions/workflows/gold-research-site.yml/dispatches";

export default {
  async scheduled(event, env, ctx) {
    const response = await fetch(DISPATCH_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_PAT}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "gold-research-trigger-worker",
      },
      body: JSON.stringify({ ref: "master" }),
    });
    if (!response.ok) {
      const text = await response.text();
      console.error("dispatch failed:", response.status, text);
      throw new Error(`GitHub dispatch failed: ${response.status}`);
    }
    console.log("dispatch ok:", response.status);
  },
};
