# SC2 Battle · 星际争霸II 赛事对战平台

一个面向《星际争霸II》合作模式 PVP 社区的赛事与天梯对战平台，采用 **FastAPI + Jinja2 + SQLite** 构建的单体 Web 应用，服务端渲染为主、Ajax 局部刷新为辅。

支持从赛事创建、报名签到、自动排表、BP（Ban/Pick）选人到 replay 上传投票判负的完整办赛流程，并附带网页天梯匹配、积分商城、头像装扮、同人小说与补丁日志等扩展模块。

---

## ✨ 主要功能

### 账号体系
- 注册 / 登录 / 持久化会话（PBKDF2 口令哈希 + 会话令牌）
- 游戏内昵称绑定、密码修改、头像设置（含 Canvas 裁剪与默认头像）
- 角色权限：超级管理员 / 天梯管理员 / 赛事管理员 / 商城管理员

### 赛事系统
- 赛事创建、报名、签到、参赛者管理
- 四种赛制：单败淘汰、双败淘汰、瑞士轮、循环赛
- 双阶段赛（如「小组循环 → 单败淘汰」）、分组出线、种子编排
- 自动排表推进、比分录入、赛事回退、赛事排行榜
- 私有赛事（邀请码）、赛事积分与积分榜

### BP 与 Replay 系统
- 多种 BP 模式：地图池 BP、随机地图+指挥官 BP、指挥官 1v1/2v2 征服者、天梯 1v1 BP、EVO BP
- 逐手限时倒计时，Ajax 轮询同步对手操作（`/bp/sync` + 局部片段刷新）
- Replay 上传归档、双方投票判定胜负、票数不一致自动触发管理员仲裁
- 投票与 Replay 并行推进流程，支持跳过上传

### 天梯系统
- 网页版 1v1 / 2v2 匹配、Elo 评分、天梯分榜
- 匹配确认倒计时、BP 流程、房间聊天（大厅频道 / 对局频道）
- 天梯悬赏、管理员仲裁与回放审查

### 扩展模块
- **积分商城**：物品上架、订单管理、xlsx 批量导出、兑换积分
- **头像装扮**：自定义头像、昵称颜色（限赞助用户）
- **赞助墙**：赞助感谢页与批量赞助用户管理
- **同人小说**：目录页自动解析正文章节并生成锚点跳转
- **补丁日志**：多版本补丁列表页，按时间戳倒序展示

---

## 🛠 技术栈

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI（ASGI） |
| ASGI 服务器 | Uvicorn |
| 模板引擎 | Jinja2（服务端渲染） |
| 数据库 | SQLite（WAL 模式，自动迁移，零外部依赖） |
| 表单 / 文件上传 | python-multipart |
| Replay 解析 | sc2reader |
| PDF 生成 | reportlab |
| 前端 | 原生 HTML / CSS / JavaScript（无框架，无构建步骤） |
| 测试 | pytest + fastapi.testclient |

---

## 📁 项目结构

```
contest/
├── app.py                  # 核心后端：路由、业务逻辑、模板上下文（单体主文件）
├── config.py               # 全部常量与路径配置，加载 .env
├── database.py             # 数据库连接与初始化
├── bp_modes.py             # BP 模式常量与标签定义
├── replay_parser.py        # Replay 解析封装
├── run_server.py           # 启动入口（默认 127.0.0.1:8010）
├── run_server_http80.py    # 启动入口（默认 80 端口）
├── requirements.txt
├── .env.example            # 环境变量模板
├── templates/              # Jinja2 模板（页面 + Ajax 局部片段）
├── static/                 # 样式、脚本、指挥官素材、商城图片
├── background/             # 头像与装饰素材
│   ├── normal/             # 默认头像
│   ├── square/             # 方形素材
│   └── rectangle/          # 矩形素材
├── novel/                  # 同人小说 HTML 与封面
├── update/                 # 版本补丁日志 HTML
├── guide_assets/           # 操作指南资源
├── deploy/
│   └── nginx/              # 生产环境 nginx 站点配置
└── ladder_project/         # 独立子工程：桌面版天梯客户端 / 服务端
    ├── client_app.py
    ├── server_app.py
    └── shared/             # 与网页端复用的常量与 Elo 逻辑
```

> **架构说明**：后端以单体为主线，`config.py` / `database.py` / `bp_modes.py` 已从 `app.py` 拆出；前端页面通过 `data-bp-partial-target` 标记支持 Ajax 片段替换，配合 `/bp/sync` 轮询接口实现实时同步。

---

## 🚀 快速开始

### 1. 环境要求
- Python 3.12+
- 无需安装数据库，SQLite 随项目自动创建

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
```bash
# 复制模板并按需修改
cp .env.example .env        # Windows: copy .env.example .env
```
`.env` 内容示例：
```ini
SUPERADMIN_USERNAME=sudo
SUPERADMIN_PASSWORD=your-strong-password

STREAMER_USERNAME=streamer
STREAMER_PASSWORD=your-streamer-password
```
> ⚠️ `.env` 已被 `.gitignore` 排除，请勿提交到版本库。首次启动时会按该配置自动创建/同步超级管理员账号。

### 4. 启动服务
```bash
python run_server.py
# 或指定端口 / 开启热重载
HOST=0.0.0.0 PORT=8010 RELOAD=1 python run_server.py
```
访问 `http://127.0.0.1:8010` 即可。

### 5. 可选：放置收款码
如需启用赞助页，将收款码图片放入 `payment/` 目录：
```
payment/wechat.png     # 微信收款码
payment/alipay.jpg     # 支付宝收款码
```
> 该目录已被 `.gitignore` 排除（含个人隐私信息）。

---

## ⚙️ 配置说明

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SUPERADMIN_USERNAME` | `sudo` | 超级管理员登录名 |
| `SUPERADMIN_PASSWORD` | `change-me-in-production` | 超级管理员密码（**务必修改**） |
| `STREAMER_USERNAME` | `streamer` | 直播/回放管理员登录名 |
| `STREAMER_PASSWORD` | `change-me-in-production` | 直播/回放管理员密码（**务必修改**） |
| `HOST` | `127.0.0.1` | 监听地址 |
| `PORT` | `8010` | 监听端口 |
| `RELOAD` | `0` | 设为 `1` 开启 Uvicorn 热重载 |

数据库、路径等其余常量集中在 `config.py` 中管理。

---

## 🧪 测试

```bash
pytest tests/ -v
```
测试覆盖 BP 流程、replay 判负、双阶段赛、天梯匹配、认证与表单可见性等场景。

> 本仓库未包含 `tests/` 目录与测试产物，请在本地维护测试文件。

---

## � 生产部署

生产环境采用 **nginx 反向代理 + Uvicorn** 的经典组合：

```
客户端 ──HTTPS──> nginx (443) ──HTTP──> Uvicorn (127.0.0.1:8010)
                    │
                    └── 静态资源 / WebSocket 直转后端
```

| 文件 | 用途 |
|------|------|
| `deploy/nginx/coop.battlesc2.top.conf` | 线上站点 nginx 配置（HTTPS + HTTP/2 + WebSocket + 200MB 上传 + 长超时） |
| `deploy/nginx/README.md` | nginx 部署步骤与要点说明 |
| `deploy_update.sh` / `deploy_update.ps1` | 版本更新时把运行时数据（数据库、头像、replay 等）迁移到新版本目录 |

**关键配置项**：`client_max_body_size 200m`（避免上传 413）、`/ws/` 单独 location 并关闭 `proxy_buffering`（保障实时推送）、`upstream keepalive`（复用后端连接）。

> 证书与私钥不随仓库分发，请在服务器上自行签发并调整 `ssl_certificate` 路径。

---

## �📌 数据库与数据安全

- 数据库文件 `contest.db` 采用 **WAL 模式**，在应用启动时自动建表并进行列级迁移（`ensure_column`），旧版本数据库可直接兼容升级。
- 以下内容已在 `.gitignore` 中排除，**不会随仓库分发**：
  - `.env`（账号口令）
  - `contest.db`（业务数据）
  - `avatars/`、`ReplaySave/`（用户上传内容）
  - `payment/`（个人收款码）
  - `tests/`、`.pytest_*`（测试文件与产物）
  - `.venv/`、`__pycache__/`、`ladder_project/build|dist`（虚拟环境与构建产物）

---

## 📄 许可与声明

- 本项目为社区自发的非商业项目，仅供学习与技术交流使用。
- StarCraft® II 及相关商标归 Blizzard Entertainment, Inc. 所有。
- 项目中的平衡性数值、指挥官素材等来源于社区整理，如涉及权利问题请联系调整。
