# Icelin 天梯系统（独立工程）

本目录是独立于水友局工程的新项目，仅包含两端：

- 服务器端: `server_app.py`
- 客户端: `client_app.py`

## 已实现能力

1. 登录注册与自动登录
- 昵称 + 密码注册/登录。
- 注册成功后客户端会自动发起登录。
- 服务器保存账号密码哈希（PBKDF2）。
- 客户端可将账密加密保存为二进制文件（`credentials.bin`）。
- 下次启动自动读取并尝试自动登录。

2. 天梯分与匹配机制
- 评分: Elo + 动态 K 值。
- 公式: `R' = R + K(S-E)`。
- 动态 K: 新号波动更大，老号更稳定。
- 匹配窗口: 初始 ±50，每 10 秒 +25，最大 ±300。
- 候选选择: 分差 + 等待时间 + 延迟 + 重复对局惩罚综合代价最小。

3. 模式
- 1v1:
  - 玩家报名时选 3 位指挥官。
  - Ban/Pick（30s 超时自动推进）:
    - Ban: 禁用对方 1 位或不禁用。
    - Pick: 从可用列表中选 1 位（可随机）。
  - 地图随机池:
    - 10000 Feet LE
    - Celestial Enclave LE
    - Mothership LE
    - Old Republic LE
    - Ruby Rock LE
    - Taito Citadel LE
    - Tourmaline LE
    - White Rabbit LE
    - Winter Madness LE

- 2v2:
  - 由发起者填写队友昵称，服务器自动绑定该队友账号。
  - 绑定期间队友匹配被临时禁用，避免多端重复匹配。
  - 同一玩家可和不同队友形成不同 2v2 天梯分（按队友组合独立）。
  - Ban/Pick（30s 超时自动推进）:
    - Team Ban: 各队可禁用对方最多 3 位（可不禁用）。
    - Team Pick: 各队选择己方 2 位，超时默认未被禁用列表前两位。
  - 地图随机池:
    - Fields of Death CE
    - Gemgarden LE
    - New Bed of Chaos LE
    - Reclamation LE
    - Rhoskallian LE
    - Rohana LE
    - Rust Bucket LE
    - Undercurrent LE
    - Yellowjacket

4. 赛后结果上报与争议处理
- 双方客户端都可上报“我方胜/对方胜”。
- 双方一致: 自动结算并更新天梯分。
- 双方不一致: 进入服务器待审列表。
- 管理员在服务器端手动判定 A 胜或 B 胜，随后更新分数。
- 服务器端提供待审比赛筛选面板，支持按时间、模式、玩家昵称筛选。

5. 临时聊天室
- 匹配成功后自动建立对局聊天室。
- 比赛结算或进入待审后聊天室关闭。

6. 实时大厅人数统计
- 服务器端实时显示在线玩家数量、1v1 匹配玩家数量、2v2 匹配玩家数量。
- 客户端同步实时显示在线玩家数量、1v1 匹配玩家数量、2v2 匹配玩家数量。

7. 数据落盘
- `server_data/users.json`: 用户与 1v1 分数。
- `server_data/pair_ratings.json`: 2v2 组合分数。
- `server_data/pending_reviews.json`: 待管理员判定比赛。
- `server_data/matches.jsonl`: 每场比赛日志记录。
- 客户端登录信息与设置:
  - 源码运行: 保存到用户目录 `~/.icelin_ladder/`。
  - `LadderClient.exe` 运行: 保存到 EXE 同目录（`client_settings.json`、`credentials.bin`）。

## 运行

在 `ladder_project` 目录分别启动：

```powershell
python server_app.py
python client_app.py
```

建议先启动服务器，再启动客户端。

## 一键打包 EXE

在 `ladder_project` 目录执行:

```powershell
.\build_exe.ps1
```

如果 `python` 指令不可用，可指定解释器:

```powershell
.\build_exe.ps1 -PythonExe "E:\ProgramData\anaconda3new\python.exe"
```

打包完成后会生成:

- `dist/LadderServer.exe`
- `dist/LadderClient.exe`
