# Nginx 反向代理配置

本项目生产环境使用 **nginx 反向代理 + HTTPS** 对外提供服务，后端为 Uvicorn（默认 `127.0.0.1:8010`）。

## 文件说明

| 文件 | 用途 |
|------|------|
| `coop.battlesc2.top.conf` | 线上站点 `coop.battlesc2.top` 的站点配置（HTTPS + HTTP/2 + WebSocket + 大上传 + 长超时） |

## 配置要点

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `upstream` | `127.0.0.1:8010` + `keepalive 32` | 与后端保持长连接，减少 TCP 重建开销 |
| HTTP 跳转 | `80 → 301 → https` | 强制 HTTPS |
| TLS | TLSv1.2 / TLSv1.3，`shared:SSL:10m` 会话缓存 | 多站点共用会话缓存，加快重复握手 |
| 上传限制 | `client_max_body_size 200m` | 默认 1MB 会导致头像/图片上传返回 413 |
| WebSocket | `/ws/` 单独 location，`Upgrade`/`Connection` 头 + `proxy_buffering off` | 缺失会导致实时推送退化为轮询 |
| 超时 | 普通请求 300s，WebSocket 3600s | 覆盖导入剧本、导出战报等长请求 |

## 部署步骤

1. **修改证书路径**（按实际服务器位置调整）：
   ```nginx
   ssl_certificate     ../ssl/coop.battlesc2.top.pem;
   ssl_certificate_key ../ssl/coop.battlesc2.top.key;
   ```

2. **放置配置文件** 到 nginx 站点目录，并确认已在主配置中 include：
   ```bash
   # 典型路径（Debian/Ubuntu）
   sudo cp coop.battlesc2.top.conf /etc/nginx/conf.d/
   # 或在 nginx.conf 的 http 块中：include /etc/nginx/conf.d/*.conf;
   ```

3. **校验并热加载**：
   ```bash
   sudo nginx -t && sudo nginx -s reload
   ```

4. **确认后端已启动**（先于 nginx 或同时）：
   ```bash
   HOST=127.0.0.1 PORT=8010 python run_server.py
   ```

## 注意事项

- **证书与私钥不随仓库分发**，请在服务器上自行签发/放置（如 Let's Encrypt 或自有证书）。
- nginx ≥ 1.25.1 时 `listen 443 ssl http2;` 会提示弃用警告，可改为：
  ```nginx
  listen 443 ssl;
  http2 on;
  ```
- 版本更新时，使用仓库根目录的 `deploy_update.sh`（Linux）或 `deploy_update.ps1`（Windows）
  迁移运行时数据（数据库、头像、replay 等）到新版本目录。
