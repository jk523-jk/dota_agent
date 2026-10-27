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

所有比赛数据默认放在 `G:\AI0707\dota_agent\dota_data`。数据库为 `dota_data\review.sqlite3`；每场比赛的录像、OpenDota 元数据、HTML 报告和 CSV 放在 `dota_data\matches\对阵\北京时间__赛事名称__比赛ID\`。例如 `matches\yandex——spirit\2026-10-04_00-52_CST__BLAST SLAM VIII__9027799260\`。导入以录像 SHA-256 去重。报告为单个 HTML 文件，不请求网络资源。

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
