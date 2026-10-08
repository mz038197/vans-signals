# Fly.io 部署（vans-signals）

## Secrets

| Secret | 說明 |
|--------|------|
| `SIGNALS_TOKENS` | 各呼叫服務的 token 雜湊，對到 Fly app 名稱 |
| `SLACK_WEBHOOK_URL` | Slack 私訊 |
| `DATABASE_URL` | Neon 專案 `VCRouter-db` 的 database `vans_signals` |

`DATABASE_URL` 現在和 router 一樣用 role `neondb_owner`，所以這組連線也打得開 `neondb`。規格 4 改成專屬 role：只能連 `vans_signals`，不能連 `neondb`。

連不上不能只靠 role 的名字。Neon 可能把 `CONNECT` 開給 `PUBLIC`，在 console 建的 role 也可能屬於 `neon_superuser`。要對 `PUBLIC` `REVOKE CONNECT`，這個 role 不能是 superuser，再只 `GRANT CONNECT` 給它。

驗收：用這組 role 連 `neondb`，連線必須被拒。role 名稱，以及這次換帳號排在 MCP 切換的哪一步，尚未定。

## 部署

`git push origin main` 不會部署這個服務。正式分支是 `master`：push `master` 會跑 `.github/workflows/fly-deploy.yml`。CI 只 deploy 程式碼，不覆寫 Fly secrets。
