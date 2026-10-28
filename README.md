# Dota 2 录像复盘

Windows 桌面复盘工具。导入本地 `.dem` 后，程序用游戏内时间（扣除暂停）组织英雄轨迹、眼位、开雾、神符、BP 和经济数据，保存为 SQLite，并可导出离线 HTML 与 CSV。

英雄改动页可同步 Valve 官方近期补丁说明，在本地 SQLite 知识库中按英雄、技能和天赋切分并建立全文索引；选中英雄后可检索其近期改动并打开原始补丁数据。技能名称会在选中对应英雄时从 Valve 英雄数据补齐。首次使用或需要更新时点击“同步英雄日志”，同步完成后可离线浏览。

当前只接收至少有一方为 **OG、Team Yandex、PARIVISION、Team Spirit、Xtreme Gaming、1w（1win）、Team Liquid、LGD 或 Tidebound** 的比赛。历史数据库中的其他比赛不会出现在桌面列表或多场趋势中。

## 运行

使用 Python 3.10+，在选定环境安装 `requirements.txt`，然后运行：

```powershell
python -m dota_review
```

本工作区使用 `G:\Anaconda3\envs\agent\python.exe` 验证；可直接在 PowerShell 中运行 `& 'G:\Anaconda3\envs\agent\python.exe' -m dota_review`。

命令行也支持批量导入及导出：

```powershell
python -m dota_review import match.dem
python -m dota_review export MATCH_KEY
```

## 网页工作台（MySQL）

本项目数据已迁移到 MySQL 的 `dota_review` 库。网页 API 使用 `dota_review_app` 账号；连接设置放在项目根目录 `.env`，该文件已加入 Git 忽略规则。新环境可参考 `.env.example` 配置 `DOTA_MYSQL_*` 项。不要把真实 `.env` 提交到仓库。

安装 `requirements.txt` 后启动 FastAPI：

```powershell
python -m dota_review.api
```

如果已经构建 Vue 页面，打开 `http://127.0.0.1:8765/` 即可。首次构建或开发前端时：

```powershell
cd frontend
npm install
npm run build
```

开发模式运行 `npm run dev`，Vite 页面默认在 `http://127.0.0.1:5173/`，并把 API 请求转发到 FastAPI。FastAPI 只绑定本机回环地址。网页支持比赛切换、时间轴播放/定位、地图图层、英雄选择、当前时刻物品栏查看和分路编辑。

网页已接入 LangGraph 复盘 Agent，提供三类证据型分析：

- **选手分析**：按 10/15/20/30 分钟展示经济、补刀和队内经济名次，并汇总该选手的眼位放置。
- **眼位热区**：按约 900 个世界坐标单位聚合双方侦查守卫/岗哨记录；可在地图叠加热点，并从来源跳回录像时间。
- **战队风格**：按已导入且队名可识别的比赛统计胜率、时长、前 15 分钟击杀、烟雾、眼位、拆塔和肉山记录。样本量会标出；目前不足以形成稳定战术结论。

Agent 通过受控只读分析函数取数，不允许模型直接执行 SQL。模型未配置时，LangGraph 会使用确定性分析和模板答复；配置模型后会让模型对同一份结构化证据做自然语言综合。可在 `.env` 设置 `DOTA_AGENT_MODEL`（如 `provider:model`），并按所选 provider 设置 `DOTA_AGENT_API_KEY` 或 provider 官方环境变量；可选 `DOTA_AGENT_BASE_URL` 用于兼容接口。provider 对应的 LangChain 集成包需另行安装。对话检查点默认保存在 `dota_data/agent-state.sqlite3`，可用 `DOTA_AGENT_STATE_DB` 修改。当前网页只绑定本机，尚未提供多用户身份与会话隔离。

当前标准化数据没有可靠的击杀者/被击杀者 slot 关联，因此选手分析不会推断个人 K/D/A。眼位热区是放置位置聚合，不代表真实战争迷雾或可见范围。战队风格只反映资料库中已导入的比赛，不做因果判断。新导入的录像会自动采集装备快照；桌面 SQLite 与网页 MySQL 目前不会自动同步。

逐秒装备快照需要先由数据库管理员执行 `scripts/upgrade_mysql_interactive.sql`，再运行 `python scripts/backfill_inventory.py` 为本地已有录像补录数据。此升级只授予玩家分路字段更新权限，以及新装备快照表的读写权限。未升级时地图和比赛详情仍可读，但装备查询和分路保存会提示不可用。

Tkinter 桌面端仍以 `dota_data\review.sqlite3` 作为本地数据源；FastAPI 网页端读取已迁移到 MySQL 的数据副本。两端目前不会自动同步，桌面端后续新增或修改的比赛尚不会自动出现在网页端。每场比赛的录像、OpenDota 元数据、HTML 报告和 CSV 仍放在 `dota_data\matches\对阵\北京时间__赛事名称__比赛ID\`。导入以录像 SHA-256 去重。报告为单个 HTML 文件，不请求网络资源。

比赛时间和赛事名称优先取自 OpenDota 比赛详情；联网失败时保留“时间待核实”或赛事 ID，之后点击“校验版本”会更新元数据并重命名比赛目录。跨场趋势 CSV 存在 `dota_data\reports`。

## 数据口径

- 首版地图机制和 BP 模型只针对 7.41f。OpenDota 的 patch ID 只到主版本 7.41，程序结合 Valve 的 7.41f 发布日与保守结束日筛选比赛，界面标注“时间窗口推定”；不能把它称为从录像直接确认的精确子版本。
- 地图昼夜按录像游戏时间推算：开局前为夜间，0:00 转白昼，此后每 5 分钟切换；地图夜间覆盖层和切换倒计时随时间轴更新。会主动改变游戏时刻的技能效果不从录像推断。
- 录像时间轴采用 gem-dota 的 `GameClock`。带采样轨迹的推断会保留采样间隔及缺失证据。
- 英雄改动知识库来源为 Valve `dota2.com/datafeed`，每次同步最近 12 个补丁版本；界面展示可检索的原始变更片段和来源链接，不对数值改动自动推测强弱或胜率影响。
- 眼位活动区间为 `[放置, 消失)`；录像截断时不推断其正常到期。
- 联动与拉野是按可调阈值产生的候选片段。开雾后发生的击杀或目标只显示为后续事件，不解释为因果。
- 若公开职业赛模型没有通过按比赛时间切分的校验，只显示“预测数据不足”。
- “训练 BP”从 OpenDota 缓存职业赛详情，按比赛时间前 80% 训练、后 20% 验证，至少 200/50 场；Brier 分数优于常数基线且 ECE 不超过 0.10 才启用。验证结束时间须早于待预测比赛。

## 已知限制

gem-dota 的公开 `ParsedMatch` 不包含完整小兵/野怪连续轨迹。程序使用它的 `ReplayParser.on_entity` 做第二遍解析，每 2 秒采样可读的兵线与野怪实体；只有满足实体证据的片段才生成兵线/拉野候选。回调无法读取的实体明确标记“证据不足”。地图底图是坐标示意图，不代表 7.41f 的地形、视野或通路。

Web 地图接口另返回录像实体读取到的防御塔、商店、肉山和魔方位置，以及 gem-dota 7.41 地图目录中的莲花池与 28 个野怪营地点。兵线按 20 秒区间汇总位置。清野和堆叠显示为候选：清野根据已有中立实体后连续两个采样点未读到实体判断；堆叠根据整分钟刷新边界前后实体数量增加判断。录像实体可能因解析器可见性或采样缺口消失，因此这些候选不等于确认击杀/拉野结果。地图要素和兵线/营地观察可由 `/api/matches/{key}` 读取；旧比赛要重新解析录像并使用 `refresh-map` 才能写入新增数据。

Web 地图的昼夜着色及英雄基础视距圈随时间轴切换；视距按标准日/夜范围作示意，不包含英雄特性、技能、地形遮挡或实际战争迷雾，因此不能用于判定某队实际看见了什么。

## 开发验证

```powershell
python -m unittest discover -s tests -v
```

公开职业赛的复盘与 OpenDota 对照见 [验证记录](docs/verification.md)。大型录像及 API 缓存只保留在本地 `dota_data`，不随源码提交。
