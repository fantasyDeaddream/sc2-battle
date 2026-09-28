# SC2 Battle 项目记忆文件

## 1. 项目定位

这是一个以 FastAPI + Jinja2 + SQLite 为核心的单体网页项目，主要提供四类能力：

- 账号系统：注册、登录、持久化会话、游戏内昵称绑定、账号设置。
- 赛事系统：创建赛事、报名签到、参与者管理、分组/种子/晋级配置、自动排表、比分录入。
- BP 与 replay 系统：多种 BP 赛制、赛事准备面板、自动/手动 replay 判定、赛事 replay 归档下载。
- 天梯系统：网页天梯 1v1/2v2 匹配、BP、聊天室、天梯分、天梯悬赏、管理员仲裁。

此外，仓库里还有一个独立子工程 `ladder_project/`，它不是当前网页主站的前端，而是另一套桌面版天梯客户端/服务端实现。网页项目会复用其中 `shared/` 目录下的一些常量和 Elo 评分逻辑。


## 2. 技术栈与运行方式

- Web 框架：FastAPI
- 模板：Jinja2
- 数据库：SQLite
- 表单/上传：python-multipart
- Replay 解析：sc2reader
- PDF 生成：reportlab
- 测试：pytest + fastapi.testclient

常用启动方式：

- `run_server.py`：默认启动在 `127.0.0.1:8010`
- `run_server_http80.py`：默认启动在 `127.0.0.1:80`

推荐本地验证环境：

- 优先使用工作区内 `.venv`
- 常用命令：`./.venv/Scripts/python.exe -m pytest ...`


## 3. 顶层目录与文件职责

### 3.1 核心源码

- `app.py`
  - 整个网页项目的单体核心文件。
  - 包含：应用初始化、数据库 schema/migration、公共 helper、赛事排表、BP 状态机、replay 判定、天梯逻辑、管理员逻辑、所有路由。
  - 修改任何网页业务，通常都要从这里入手。

- `bp_modes.py`
  - 纯 BP 逻辑库。
  - 定义 BP 模式常量、模式标签、地图池 BP 模式、随机地图+指挥官 draft 的规则与操作 helper。
  - 当前已定义：`none`、`map_pool`、`commander_draft`、`commander_conquest_1v1`、`ladder_1v1_bp`、`evo_bp`。

- `replay_parser.py`
  - 纯 replay 解析与严格判定逻辑。
  - 负责游戏昵称规范化、时间解析、地图名比较、玩家/队伍与 replay 阵营匹配、严格胜负裁定。
  - 所有 replay 自动判定的“最终严规则”都应优先改这里，而不是在路由层写分散补丁。

- `run_server.py`
  - Uvicorn 启动器，默认端口 8010。

- `run_server_http80.py`
  - Uvicorn 启动器，默认端口 80。

- `requirements.txt`
  - 依赖列表：FastAPI、Uvicorn、Jinja2、python-multipart、sc2reader、pytest、reportlab。


### 3.2 模板目录 `templates/`

- `base.html`
  - 全站基础布局。
  - 顶部导航、当前用户状态、统一样式入口、`app.js` 加载入口都在这里。
  - 也是 `is_superadmin`、`is_streamer`、`commander_asset` 等全局模板上下文的消费点。

- `auth.html`
  - 登录/注册页。

- `hub.html`
  - 登录后入口分流页。

- `dashboard.html`
  - 赛事大厅列表页。

- `tournament_form.html`
  - 创建赛事表单。
  - 新赛制、新 BP 模式、新全局参数，通常需要同步修改此文件和 `app.py` 中的 `create_tournament`。

- `tournament_detail.html`
  - 赛事详情总页面。
  - 集成：赛事信息、报名管理、我的对阵、对阵表、管理员观察面板。

- `_tournament_my_matches_panel.html`
  - “我的对阵”局部面板。
  - 是赛事选手操作准备、BP、上传 replay 的核心 UI 片段。

- `_replay_series_panel.html`
  - 赛事 replay 上传、手动判定、管理员仲裁 UI 片段。

- `_replay_inspector_panel.html`
  - replay 解析结果展示片段。

- `_ladder_match_panel.html`
  - 天梯页面中的当前队列/当前比赛局部面板。
  - 由 `/ladder/partial` 用于局部刷新。

- `_commander_macros.html`
  - 指挥官标签、缩略图、展示卡片的共用宏。
  - 任何模板 import 它时必须使用 `with context`，否则拿不到 `commander_asset` helper。

- `ladder.html`
  - 网页天梯大厅页。

- `ladder_bounties.html`
  - 天梯悬赏页。

- `admin.html`
  - 超级管理员后台。

- `replay_test.html`
  - replay 解析测试工具页。

- `guide.html`
  - 操作指南页面。

- `streamer_bp_replays.html`
  - 主播/超管可访问的 BP 回放控制台页面。

- `account_settings.html`
  - 账号资料与密码修改页。

- `_info_partial.html`
  - 赛事信息标签页（管理员观察面板）的局部刷新片段。
  - 由 `/tournaments/{id}/info/partial` 渲染，用于 Ajax 轮询替换，避免整页重载。

- `_bracket_partial.html`
  - 对阵表标签页的局部刷新片段。
  - 由 `/tournaments/{id}/bracket/partial` 渲染，用于 Ajax 轮询替换。


### 3.3 静态目录 `static/`

- `app.js`
  - 全站唯一主脚本。
  - 负责：登录页 tab、赛事 BP 自动轮询与局部刷新、滚动位置恢复、倒计时、天梯大厅聊天、在线人数、榜单切换、主播 BP 回放逐帧播放。
  - 如果页面交互异常，优先检查这里和模板上的 `data-*` 属性是否匹配。

- `style.css`
  - 全站样式。
  - 定义布局、卡片、按钮、BP 标签、天梯面板、主播回放页、指挥官图片组件等视觉规则。

- `commander_assets/`
  - 指挥官图片资源根目录。
  - `manifest.json`：名称到图片路径的绑定表。
  - `Commander_Figures/`：当前实际使用的原始图片目录。
  - `thumbs/`、`full/`：保留的可选目录；当前默认不强制拆两套图。
  - `README.txt`：图片资源使用说明。


### 3.4 指南与生成物

- `guide_assets/operation_guide.pdf`
  - 网页“操作指南”固定读取的 PDF。

- `guide_assets/generate_operation_guide.py`
  - 用 reportlab 生成操作指南 PDF 的脚本。

- `guide_assets/README.txt`
  - 说明该目录是固定资源目录，可以直接替换 PDF。


### 3.5 测试目录 `tests/`

- `test_auth_flow.py`
  - 登录、注册、会话、基础赛事流的认证相关测试。

- `test_bp_flow.py`
  - 赛事 BP 主回归文件。
  - 覆盖 map_pool、commander_draft、ladder_1v1_bp、evo_bp、commander_conquest_1v1、主播 BP 回放页等。

- `test_ladder_flow.py`
  - 网页天梯、匹配、BP、聊天、replay、悬赏等测试。

- `test_replay_flow.py`
  - replay 严格判定与赛事 replay 相关测试。

- `test_double_stage_flow.py`
  - 双阶段赛、分组、晋级与第二阶段排表测试。

- `test_guide_flow.py`
  - 操作指南页面和 PDF 入口测试。


### 3.6 独立子工程 `ladder_project/`

这是单独的桌面版天梯系统，不是当前网页项目运行所必需，但网页项目会复用其共享逻辑。

- `ladder_project/server_app.py`
  - 桌面版天梯服务端。

- `ladder_project/client_app.py`
  - 桌面版天梯客户端。

- `ladder_project/shared/constants.py`
  - 共享常量：默认 host/port、BP 时间、匹配窗口、指挥官池、地图池。
  - 网页天梯直接 import 这里的常量。

- `ladder_project/shared/rating.py`
  - Elo 与动态 K 值实现。
  - 网页天梯的积分更新直接复用这里的 `update_elo`。

- `ladder_project/shared/net.py`
  - 独立工程网络通信封装。

- `ladder_project/shared/security.py`
  - 独立工程安全/加密工具。

- `ladder_project/build_exe.ps1`、`.spec`、`dist/`、`build/`
  - 桌面工程打包产物与脚本。


### 3.7 独立子工程 `remove_chat_tool/`（已搁置）

独立的桌面工具，尝试从 SC2 replay 中移除玩家聊天。与网页项目无关。

- `remove_chat_tool/remove_chat.py` — 核心逻辑
- `remove_chat_tool/remove_chat_gui.py` — Tkinter GUI
- `remove_chat_tool/dist/RemoveChat.exe` — 打包产物
- **状态**：mpyq/sc2reader 可读取输出文件，但 SC2 客户端打开即崩溃。多次尝试均无法解决。已搁置。


### 3.8 运行期/数据文件

- `contest.db`
  - SQLite 数据库。

- `ReplaySave/`
  - 赛事 replay 保存目录。

- `rank/`
  - 网页天梯 replay 归档目录。

- 各类 `.pytest_*.xml`、`.txt`、`pytest_replay.log`
  - 本地测试输出/临时报告，不属于业务逻辑。


## 4. app.py 的内部模块地图

`app.py` 不是简单路由文件，而是单体业务核心。阅读顺序建议如下。

### 4.1 启动与全局配置

- 定义 `BASE_DIR`、`DB_PATH`、`REPLAY_SAVE_DIR`、`RANK_SAVE_DIR`、`COMMANDER_ASSET_*` 等路径。
- 定义超级管理员与主播固定账号：`sudo`、`streamer`。
- 创建 FastAPI 应用、挂载 `/static`、初始化 Jinja 全局。
- `lifespan()` 会调用 `init_db()`，所以 schema 初始化与迁移是在应用启动时完成的。


### 4.2 数据库 schema 与轻迁移

`init_db()` 负责：

- 建表：`users`、`sessions`、`tournaments`、`tournament_admins`、`registrations`、`tournament_matches`、`tournament_round_settings`、`tournament_stage_groups`、`tournament_match_preparations`、`ladder_pair_ratings`、`ladder_queue_entries`、`ladder_matches`、`ladder_chat_messages`、`ladder_lobby_chat_messages`、`ladder_online_presence`、`ladder_bounties`、`ladder_bounty_submissions`。
- 通过 `ensure_column()` 和 `ensure_registrations_schema()` 做轻量迁移。
- 数据回填：默认值修补、`backfill_registration_seed_order()`。
- 固定账号初始化：`ensure_superadmin()`、`ensure_streamer_account()`。
- 固定目录初始化：`guide_assets/`、`static/commander_assets/thumbs/`、`static/commander_assets/full/`。


### 4.3 通用基础能力

- 时间：`now_text()`、`current_utc8_datetime()` 等。
- 密码与会话：`password_hash()`、`password_matches()`、`token_digest()`、`set_session_cookie()`、`clear_session_cookie()`、`get_current_user()`。
- 渲染：`render()` 统一给模板注入全局上下文。
- 权限：`is_superadmin_user()`、`is_streamer_user()`、`has_ladder_admin_access()`、`has_bp_replay_access()` 等。
- 指挥官图片：`load_commander_asset_manifest()`、`commander_asset_payload()`。


### 4.4 赛事核心模型与排表

这一块负责“赛事本身如何组织”。

- 赛事装载：`load_tournament_row()`、`load_tournament_bundle()`。
- 报名/签到/参与者：`load_registrations()`、`load_registration_for_user()`、`active_registration_rows()`。
- 分组/种子：`normalize_group_number()`、`ensure_stage_group_settings()`、`swap_registration_seed_order()`。
- BO 规则：`load_round_best_of()`、`upsert_round_best_of()`、`validate_best_of()`。
- 对阵生成：
  - 单败：`rebuild_single_elimination_schedule()`
  - 双败：`rebuild_double_elimination_schedule()`
  - 瑞士轮：`rebuild_swiss_schedule()`、`rebuild_swiss_advancement_schedule()`
  - 循环赛：`rebuild_round_robin_schedule()`
  - 总调度：`rebuild_format_schedule()`、`ensure_tournament_matches()`
- 对阵渲染：`match_view()`、`build_bracket_state()`、`build_my_matches_state()`。

关键约定：

- `bracket_type` 不只是简单的 `winners/losers`，而是编码成 `s1g2:winners` 这种格式，表示 stage 1、group 2、胜者组。
- 这让项目无需迁移 `tournament_matches` 表结构也能支持双阶段和分组。


### 4.5 赛事准备、BP 与状态机

赛事小局准备状态保存在 `tournament_match_preparations.state_json`。

核心入口：

- `default_match_preparation_state()`
- `load_match_preparation_state()`
- `initialize_match_preparation_state()`
- `sync_match_preparation_state()`
- `build_match_preparation_view()`
- `save_match_preparation_state()`

公共状态字段：

- `bp_mode`
- `status`
- `ready_registration_ids`
- `advance_ready_ids`
- `current_game_number`
- `current_game_ready_at`
- `step_started_at`
- `step_deadline_seconds`
- `map_bp`
- `commander_games`
- `conquest_series`
- `ladder_games`
- `evo_choices`
- `game_results`
- `pending_manual_replay_judgment`
- `pending_admin_replay_judgment`


### 4.6 各 BP 模式如何落地

#### `none`

- 不做 BP。
- 双方点击准备后可直接开打。
- 如果赛事开启 replay 自动判定，则每局通过上传 replay 推进系列赛。

#### `map_pool`

- 先走整场地图池 veto/pick。
- 使用 `bp_modes.build_map_bp_state()`、`apply_map_bp_action()`、`finalize_map_bp_if_needed()`。

#### `evo_bp`

- 先确定整场地图顺序，再在后续每小局由上一局败者选择 EVO 模式。
- 必须依赖 replay 自动判定，因为下一局选择者取决于上一局败者。

#### `commander_draft`

- 每小局独立执行：分数上限、Ban、ABBA Pick。
- 进度保存在 `commander_games`。
- 核心 helper 多在 `bp_modes.py`。

#### `commander_conquest_1v1`

- 每场 BO 默认 BO5。
- 双方先各自预选 `first_to` 个指挥官。
- 每小局从自己剩余可用池里选出战指挥官。
- 某方某指挥官赢过后，该方后续局不能再选它。
- 进度保存在 `conquest_series`。
- 因为要根据每小局真实胜者锁定指挥官，所以强制要求 `replay_result_enabled`。
- **逐局准备**：整场 BO 第一局前的准备只执行一次（预选池阶段），从第二局开始，每小局 replay 上传后自动回到 `awaiting_ready`，需要双方再次点击"准备比赛"才会进入 BP。这与 `commander_draft` 的逐局准备行为一致。

#### `ladder_1v1_bp`

- 把网页天梯 1v1 BP 逻辑移植到赛事中，并且每个小局都执行一次。
- 进度保存在 `ladder_games`。
- 不开 replay 时，双方通过“下一局”按钮推进；开 replay 时自动根据 replay 进入下一局。


### 4.7 Replay 判定与系列赛推进

赛事 replay 相关的主链路：

- 上传入口：`/tournaments/{tournament_id}/matches/{match_id}/replay`
- replay 合法性校验：
  - `replay_match_validation_error()`
  - `replay_match_context_validation_error()`
  - `current_match_expected_map_name()`
- 系列赛记录：`game_results`
- 手动裁定：`pending_manual_replay_judgment`
- 管理员仲裁：`pending_admin_replay_judgment`
- 自动推进：`advance_replay_series_state()`
- 单局结算入口：`finalize_match_replay_game()`

严格判定原则：

- 必须匹配当前局的预期地图。
- replay 开始时间不能早于当前局 BP/准备完成时间。
- 若双方绑定了相同昵称，自动判定会转为“玩家手动选赢家”。


### 4.8 网页天梯系统

这部分是 `app.py` 中另一大块独立子系统。

能力包括：

- 队列：1v1、2v2
- 在线状态与大厅统计
- 1v1/2v2 不同 BP 流程
- 临时聊天频道和大厅频道
- replay 自动结算天梯分
- 管理员介入
- 个人 replay 归档到 `rank/`
- 天梯悬赏发布/提交/审核/下载归档

关键数据表：

- `ladder_queue_entries`
- `ladder_matches`
- `ladder_pair_ratings`
- `ladder_chat_messages`
- `ladder_lobby_chat_messages`
- `ladder_online_presence`
- `ladder_bounties`
- `ladder_bounty_submissions`

关键 helper：

- 匹配与超时：`sync_ladder_state()`、`sync_ladder_matchmaking()`、`sync_ladder_timeouts()`
- 匹配对象评分：`ladder_pick_best_pair()`、`ladder_queue_pair_cost()`
- BP：`ladder_transition_to_bp_start()`、`ladder_transition_to_pick()`、`ladder_transition_to_ready()`
- replay 结算：`ladder_upload_replay()`、`ladder_resolve_match()`、`ladder_apply_rating_for_match()`
- 大厅上下文：`build_ladder_page_context()`
- 悬赏页面上下文：`build_ladder_bounties_context()`


### 4.9 主播 BP 回放控制台

这一块是赛事 BP 的只读增强层。

- 路由：`/streamer/bp-replays`
- 权限：仅 `streamer` 或超管可访问
- 数据来源：从 `tournament_match_preparations.state_json` 反推时间线，而不是读取 replay 文件
- 关键函数：
  - `list_bp_replay_match_options()`
  - `build_match_bp_replay_payload()`
  - `append_bp_replay_frame()`

如果要增强 BP 回放页面，优先改：

- `app.py` 中的 payload 生成函数
- `templates/streamer_bp_replays.html`
- `static/app.js` 中 `data-bp-replay-root` 对应的播放器逻辑


### 4.10 管理后台与工具页

- 超管后台：`/admin`
  - 用户删除
  - 用户角色调整
  - 天梯待审比赛裁定
  - 赛事删除
  - replay 测试结果展示

- Replay 工具页：`/tools/replay`
  - 普通登录用户可测试自己的 replay 解析结果。


## 5. 路由分组总览

按模块可以把 `app.py` 中的路由理解成以下几组：

- 基础页：`/`、`/hub`、`/guide`、`/guide/pdf`
- 主播页：`/streamer/bp-replays`
- 认证与账号：`/auth/*`、`/account/*`
- 赛事大厅：`/dashboard`
- 网页天梯：`/ladder*`
- 超管与工具：`/admin*`、`/tools/replay*`
- 赛事主流程：
  - 创建与详情：`/tournaments/new`、`/tournaments`、`/tournaments/{id}`
  - 报名/签到/退出：`/register`、`/withdraw`、`/checkin`
  - 参与者管理：昵称、签到、移除、临时选手、分组、晋级、种子
  - 赛事开始与对阵发布：`/start`、`/publish-bracket`
  - BO 设置：`/round-settings`、`/format-best-of-settings`
  - 我的对阵/局部刷新：`/bp/sync`、`/my-matches/partial`、`/info/partial`、`/bracket/partial`
  - 比赛准备/BP/replay：`/prepare`、`/bp/action`、`/bp/advance`、`/replay*`
  - 赛事 replay 总归档：`/replay-archive`


## 6. 模板与后端的映射关系

- 赛事详情大页由 `tournament_detail()` 渲染 `tournament_detail.html`
- 赛事“我的对阵”局部刷新由 `tournament_my_matches_partial()` 渲染 `_tournament_my_matches_panel.html`
- 天梯局部刷新由 `ladder_partial()` 渲染 `_ladder_match_panel.html`
- 主播回放由 `streamer_bp_replays()` 渲染 `streamer_bp_replays.html`
- replay 上传/仲裁面板通过 `_replay_series_panel.html` 被多个页面 include

这意味着：

- 如果 BP 页面刷新后状态不对，通常不是整页模板问题，而是“后端 view builder 与 partial 模板不匹配”。
- 如果页面首屏对，但自动刷新后错，优先检查 `/bp/sync`、`/my-matches/partial`、`/ladder/partial` 返回的数据/片段。


## 7. 前端交互约定

`static/app.js` 的设计特点：

- 没有使用前端框架，全部依赖服务端渲染 + `data-*` 属性绑定。
- BP 页面靠轮询同步，不是 WebSocket。
- 自动刷新时会尝试保留滚动位置、准备按钮观察窗口、天梯聊天折叠状态、比赛面板聚焦状态。
- 主播 BP 回放页面直接读模板内嵌的 JSON，并在浏览器里做逐帧切换。

因此修改 UI 时要同时关注：

- HTML 结构是否还保留了原来的 `data-*`
- `app.js` 选择器是否仍能找到节点
- 局部刷新的 partial 是否包含同步所需属性，例如 `data-bp-next-sync-url`、`data-bp-next-sync-key`


## 8. 指挥官图片系统

图片系统并不是单独的后端服务，而是一个轻量 manifest 映射：

- 后端 helper：`commander_asset_payload()`
- 模板宏：`_commander_macros.html`
- 资源配置：`static/commander_assets/manifest.json`

当前支持两种写法：

- 字符串：同一张图同时作为缩略图和大图
- 对象：显式区分 `thumbnail` 和 `full`

当前仓库已经采用单图复用方案，`Commander_Figures/` 下的图被用于不同尺寸的 UI 缩放展示。


## 9. 测试策略与验证习惯

项目目前已经有较完整的窄回归测试，优先跑对应模块的文件：

- 认证/基础页：`tests/test_auth_flow.py`
- BP 与赛事准备：`tests/test_bp_flow.py`
- 双阶段赛：`tests/test_double_stage_flow.py`
- replay 判定：`tests/test_replay_flow.py`
- 网页天梯/悬赏：`tests/test_ladder_flow.py`
- 操作指南：`tests/test_guide_flow.py`

建议修改后优先选择最窄验证：

- 改赛事 BP：先跑 `tests/test_bp_flow.py -k ...`
- 改天梯：先跑 `tests/test_ladder_flow.py -k ...`
- 改 replay：先跑 `tests/test_replay_flow.py -k ...`


## 10. 修改导航：改什么应该看哪里

### 10.1 想改登录、角色、主播账号

看：

- `app.py` 中用户/schema/session/helper
- `templates/auth.html`
- `templates/account_settings.html`
- `templates/base.html`

### 10.2 想新增赛事创建参数或赛制入口

看：

- `app.py` 的 `create_tournament()`、`tournament_form_context()`
- `templates/tournament_form.html`
- 若涉及默认 BO，也看 `default_best_of_for_tournament_row()`

### 10.3 想改排表逻辑

看：

- `ensure_tournament_matches()`
- `rebuild_*_schedule()` 系列函数
- `build_bracket_state()`

### 10.4 想改赛事 BP 规则

看：

- `bp_modes.py`
- `app.py` 中 preparation 状态机与 `apply_match_bp_action()`
- 对应模板：`_tournament_my_matches_panel.html`、`tournament_detail.html`

### 10.5 想改 replay 严格校验

看：

- `replay_parser.py`
- `app.py` 中 `replay_match_context_validation_error()`、`finalize_match_replay_game()`

### 10.6 想改网页天梯行为

看：

- `app.py` 中 `build_ladder_page_context()`、`sync_ladder_state()`、`ladder_upload_replay()`
- `templates/ladder.html`
- `_ladder_match_panel.html`
- `static/app.js`

### 10.7 想改天梯悬赏

看：

- `app.py` 中 `build_ladder_bounties_context()` 与 `/ladder/bounties/*` 路由
- `templates/ladder_bounties.html`

### 10.8 想改主播 BP 回放体验

看：

- `app.py` 中 `build_match_bp_replay_payload()`
- `templates/streamer_bp_replays.html`
- `static/app.js`
- `static/style.css`

### 10.9 想改指挥官图片或资源路径

看：

- `static/commander_assets/manifest.json`
- `templates/_commander_macros.html`
- `app.py` 中 `commander_asset_payload()`

### 10.10 想改操作指南

最简单：

- 直接替换 `guide_assets/operation_guide.pdf`

想重新生成：

- 改 `guide_assets/generate_operation_guide.py`


## 11. 关键约定与易踩坑

- `app.py` 是单体文件，改动时要先确定自己在改“数据结构”还是“模板视图”，不要同时大范围扫射。
- `tournament_match_preparations.state_json` 是 BP/replay 的真实状态源，很多页面都是从这里派生 view。
- `bracket_type` 已编码 stage/group 信息，处理对阵时不要直接假设只有 `winners/losers/finals`。
- 瑞士轮/循环赛与淘汰赛的 BO 规则不同；循环赛允许偶数 BO，其它通常默认奇数。
- `commander_conquest_1v1` 和 `evo_bp` 都依赖 replay 自动判定；不要轻易移除这个约束，否则局间推进会失真。
- Jinja 宏文件 import `_commander_macros.html` 时必须 `with context`。
- 天梯在线状态只在 `/ladder` 页面活跃；离开天梯页会清除在线 presence。
- replay 时间存储与计算大量使用 UTC 字符串，但界面展示偏向 UTC+8；比较时间时优先用 `replay_datetime_value()` 统一。
- 主播 BP 回放页面不是读取 replay 文件，而是读取 preparation state 生成的 timeline；改这里不要误以为需要碰 `ReplaySave/`。
- CSS 中严禁使用全局 `label { display: grid }` 样式——会泄漏到 `.checkbox-pill` 等特殊标签内部。正确写法：`form label:not(.checkbox-pill):not(.checkbox-row)`。
- `build_match_bp_replay_payload()` 函数参数 `game_number` 会被征服者循环变量覆盖，已改用 `_filter_game_number` 保存；新增 BP 模式时注意同名变量问题。


## 12. 已知 Bug 修复记录

### 12.1 `bracket_conquest_bp.side_names` 模板 Bug

- **症状**：`jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'side_names'`，出现在 `tournament_detail.html` 第 210 行附近。
- **根因**：`bracket_commander_conquest_snapshot()` 的快照里，`side_names` 存放在每个 game 对象内部（`game.side_names`），而非 `bracket_conquest_bp` 顶层。模板错误地使用了 `match.preparation.bracket_conquest_bp.side_names[side]`。
- **修复**：在 `tournament_detail.html` 中 3 处使用位置，改为从 `game.side_names[side]` 取值。

### 12.2 天梯指挥官勾选框排版偏移

- **症状**：天梯匹配模块中，指挥官勾选框的文本、图片和勾选框靠按键右侧，布局不齐。
- **根因**：
  1. 全局 `label { display: grid }` 规则干扰了 `.checkbox-pill` 的 `display: flex`；虽然 class 选择器理论上优先于元素选择器，但在某些浏览器 / 级联场景下 grid 样式泄漏到 pill 内部。
  2. `.checkbox-pill` 规则特异性不够强，缺少 `flex-direction: row`、`width`、`box-sizing` 等明确约束。
- **修复（第二版，已验证）**：
  - `static/style.css` 中：
    - 全局 `label { display: grid }` → `form label:not(.checkbox-pill):not(.checkbox-row) { display: grid }`，将 grid 样式限定在表单标签内，同时排除 pill 和 checkbox-row 两种特殊标签。
    - `.checkbox-pill` 升级为 `label.checkbox-pill` 双选择器，`display: flex !important` 强制覆盖。
    - 新增 `flex-direction: row`、`cursor: pointer`、`width: 100%`、`box-sizing: border-box`，input 增加 `width: auto`、`accent-color`。

### 12.3 征服者 BP：双方操作完成前隐藏对方结果

- **需求**：指挥官预选池阶段和每小局出战选择阶段，在双方都完成当前轮操作之前，不提前向对手展示已提交的选择。
- **实现**（`app.py` 中 `build_match_preparation_view()` 征服者段）：
  - 计算 `both_pool_submitted`（双方是否都已提交预选池）和 `both_picked_current`（双方是否都已选择本局出战指挥官）。
  - 构建 `view_pool_submissions`：若双方未都提交，则将对手的预选池置为空列表。
  - 构建 `view_current_game`：若双方未都选择，则将对手的 pick 置为 `None`。
  - `conquest_games` 中当前局的 pick 同样遮蔽对手未完成的选择。
  - 历史小局的 pick 始终完全可见（已结算）。
- **测试更新**：`test_commander_conquest_defaults_to_bo5_and_locks_winning_commander` 适配了 replay 后 `awaiting_ready` 的新状态流。

### 12.4 征服者地图去重逻辑

- **需求**：地图池唯一数量 >= BO 轮数时不重复使用已选地图；唯一数量 < BO 轮数时用完所有地图后可随机重复。
- **实现**（`build_commander_conquest_game_state()`）：
  - `remaining_maps` 首先排除已使用的地图名；若列表为空（已用尽全部唯一地图），回退到完整地图池允许重复。
  - 添加注释说明逻辑，实际代码无需改动（原逻辑已正确）。

### 12.5 BP 回放逐局过滤与重组

- **需求**：在现有"按比赛选择"基础上，新增小局下拉列表；始终显示打满 BO 的所有局号选项以免剧透；选择某小局后仅展示该局 BP 帧，最后一页为 BP 结果汇总（不显示比赛胜负）。
- **征服者逐局重组规则**：
  - 第 1 局回放：双方预选池帧 → 第 1 局出战选择帧 → 第 1 局 BP 总结（仅地图+出战，无胜者）
  - 第 N 局回放（N≥2）：第 N-1 局"征服者锁定"帧作为首页 → 第 N 局出战选择帧 → 第 N 局 BP 总结（无胜者）
  - 锁定帧作为下一局的开篇，交代"谁用什么指挥官赢了上一局、该指挥官已被锁定"的上下文
  - 完整回放不受影响，仍按时间线展示全部帧（含锁定和胜者信息）
- **其他 BP 模式**：按局号简单过滤对应帧 + 该局总结（无胜者）
- **已修复的 Bug**：
  - 参数解析：`game_number=""` 导致 FastAPI `int_parsing` 报错。改为 `str` 类型接收，手动转换为 `int | None`。
  - 变量覆盖：`build_match_bp_replay_payload(game_number)` 参数被征服者帧构建循环中的 `for game in ... game_number = int(...)` 覆盖，导致过滤逻辑总是使用最后一局的局号。修复：参数值在函数入口保存为 `_filter_game_number`。
- **实现文件**：`app.py`（路由 + `build_match_bp_replay_payload`）、`templates/streamer_bp_replays.html`（小局下拉列表）


## 13. Ajax 局部刷新架构说明

### 13.1 整体设计

赛事详情页（`tournament_detail.html`）原本存在多处整页刷新。现在采用了 "Ajax 轮询 + 局部 HTML 替换" 的方案：

- **轮询驱动**：`/tournaments/{id}/bp/sync` 返回 `bp_sync_state_json`，携带每个标签页的局部 URL 和目标选择器。
- **前端替换**：`app.js` 根据 `data-bp-partial-target` 属性，将服务端返回的 HTML 片段注入对应 DOM 节点。
- **新增局部端点**：
  - `/tournaments/{id}/info/partial` → 渲染 `_info_partial.html`
  - `/tournaments/{id}/bracket/partial` → 渲染 `_bracket_partial.html`
  - `/tournaments/{id}/my-matches/partial` → 渲染 `_tournament_my_matches_panel.html`（已有）

### 13.2 模板约定

- 每个可局部刷新的区域必须包裹在 `<div data-bp-partial-target="<target-name>">` 中。
- 对应的 partial 模板在根级元素上也要声明相同的 `data-bp-partial-target` 属性。
- `build_bp_sync_view()` 返回的 `partial_url` 和 `partial_target` 字段驱动前端轮询目标。

### 13.3 适用页面

- **赛事详情页**：信息标签页、对阵表标签页、我的对阵标签页均通过 Ajax 局部刷新。
- **不接受局部刷新的页面**：管理后台 `/admin`、Dashboard `/dashboard`、天梯 `/ladder`（已有独立轮询机制）等无实时内容需求的页面无需改造。


## 14. 给后续 AI 的建议阅读顺序

如果你第一次接手这个仓库，推荐按以下顺序读：

1. 先读本文件，建立目录、模块和数据流认知。
2. 再读 `app.py` 的顶部常量、`init_db()`、`render()`。
3. 接着读 `ensure_tournament_matches()`、`build_match_preparation_view()`、`apply_match_bp_action()`、`upload_match_replay()`。
4. 如果任务是天梯，再读 `build_ladder_page_context()`、`sync_ladder_state()`、`ladder_upload_replay()`。
5. 如果任务是 BP 规则，再读 `bp_modes.py`。
6. 如果任务是 replay 规则，再读 `replay_parser.py`。
7. 最后看对应模板和测试文件，确认页面入口与回归范围。


## 15. 总结

这个项目的核心特征不是“文件很多”，而是“`app.py` 把大多数业务都集中在一起”。真正修改时要抓住三条主线：

- 赛事主线：创建赛事 -> 报名签到 -> 排表 -> 我的对阵 -> BP -> replay/比分 -> 完赛
- 天梯主线：进入队列 -> 匹配成功 -> 确认 -> BP -> replay -> 积分更新 -> 悬赏复用 replay
- 资源/UI 主线：模板渲染 -> `app.js` 局部刷新/播放器 -> `style.css` 统一视觉 -> commander manifest 绑定图片

只要先判断自己在这三条线里的位置，再反查本文件列出的改动入口，基本可以快速定位到正确文件与正确修改面。