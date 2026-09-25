# 降采样层级与保留策略（从 0 实现）

仓库里只有这份说明、`samples/**` 与 `.gitignore`，没有实现代码。交付：仓库根目录的 `rollkeep` 包（Python 3.13，标准库）、命令行 `python -m rollkeep`、样例生成的 `layers.html`、`tests/`（`unittest`）。

## 1. 范围

要做：一条时间序列的降采样与保留——写入原始点时算好分钟级、小时级、天级；查询按时间范围决定取哪几层，拼成结果；页面把每层覆盖区间和这次查询的取层明细摊开给人看。

不做：多序列；乱序与重复点的归并；插值补点；压缩编码与落盘格式；分布式与实时流；鉴权与并发控制；时区换算（一律 UTC 秒）；联网、第三方依赖与构建步骤。

## 2. 口径与公式

时间一律是整数秒（UTC），区间一律是闭区间 `[起, 止]`；水位 `W` 是最后一个原始点的 `t`（点按 `t` 严格递增写入，所以也是最大 `t`）。

### 2.1 层级与保留期

`raw` 每点一条、留 3 天（259200 秒）；`minute` 60 秒桶、留 14 天（1209600 秒）；`hour` 3600 秒桶、留 120 天（10368000 秒）；`day` 86400 秒桶、留 400 天（34560000 秒）。桶按绝对时间对齐：宽 `w` 的桶是 `[w*k, w*k+w-1]`。

### 2.2 聚合

每个桶存 `count`、`sum`、`min`、`max`。平均不单独存，由 `sum / count` 现算：`decimal.Decimal` 除完做 `ROUND_HALF_UP`，输出固定 3 位小数。原始点每条 `count = 1`、`sum = min = max = v`。

### 2.3 保留与裁剪

水位每推进一次就裁剪一次，按整桶判断：层 L 保留所有「桶尾 ≥ W − 保留期」的桶，原始层保留 `t ≥ W − 259200` 的点。粗层因此向左多留不到一个桶宽，多留的桶不参与回答（见 2.4）；水位推进后超期的点会被删掉，之后不再出现。

### 2.4 拼接与去重

查询按细到粗回答，四层的回答区间首尾相接：`raw` = `[W-259200, W]` 里的原始点，`minute` = `[W-1209600, W-259201]` 里的分钟桶，`hour` = `[W-10368000, W-1209601]` 里的小时桶，`day` = `[W-34560000, W-10368001]` 里的天桶。

① 更细的层先回答，粗层只回答更细层没回答过的时间点，一个时间点只被一层回答；② 每层只输出完整落在自己回答区间、且完整落在 `[from, to]` 里的桶（原始点是点本身），被端点切掉一半的桶整桶丢弃，不按比例折算；③ 早于 `W-34560000` 的部分没有层覆盖，结果里就是空的。

## 3. 数据结构与写入流程

存储按层分开：原始层按 `t` 升序存点，三层各按 `k = 时间 // 桶宽` 建索引，一个桶一条记录。写入时校验 `t` 严格递增（乱序或重复即输入不可用），并入三层对应桶，推进水位 `W` 并裁剪。粗层必须在写入时算好；查询只读自己输出的条目与层元数据，不许重算粗层或为回答粗层区间去扫原始点。

## 4. 输入输出与文件格式

样例与所有输出都是 UTF-8 无 BOM、LF 行尾、末行有换行。

### 4.1 原始点 `samples/points/*.jsonl`

一行一个对象：`{"t": <整数秒>, "v": <非负整数>}`，`t` 严格递增不重复。`samples/points/scale.md` 说明百万级那份怎么生成，数据不入库。

### 4.2 查询 `samples/queries/*.jsonl`

一个文件一条查询，文件里只有一个对象，例如 `{"id": "raw-minute-seam", "points": "samples/points/near.jsonl", "from": 1787961000, "to": 1787962200}`。

`id` 全仓库唯一；`points` 是原始点路径（相对当前工作目录）；`from ≤ to` 是闭区间；样例都合法。

### 4.3 命令行

`python -m rollkeep query --queries samples/queries [--out var/queries.tsv]`、`python -m rollkeep stats --points samples/points/span.jsonl`、`python -m rollkeep render --query samples/queries/four-layers.jsonl --html layers.html`。

`query` 把目录里的查询文件按文件名字典序跑完，给单个文件就只跑它；stdout 每行 9 列 TAB 分隔：`<id> <层代号> <起点> <终点> <count> <sum> <min> <max> <平均>`，同一查询内按时间升序，不同查询之间不排序；给了 `--out` 就写一份与 stdout 逐字节相同的文件。

`stats` 输出 4 行（顺序 `raw`、`minute`、`hour`、`day`）：`<层代号> <条目数> <最早起点> <最晚终点>`；原始层按点算，空层写 `0` 与 `-`。

`render` 画一条查询，写单文件 `layers.html`：内联数据、不引外部资源、不含 `<script>`、双击可开、无交互；交付时仓库根目录要有一份用 `four-layers` 生成的。数字与色带只能来自引擎统计：`id="bands"` 里每层一个 `<rect data-layer data-start data-end data-retention-start data-buckets>`（前两项是保有区间首尾，`data-retention-start` = `W − 保留期`，`data-buckets` 是该层条目数）；`id="detail"` 里每层一行 `<tr data-layer data-buckets data-points>`（`data-buckets` 是该层这次查询的桶数、原始层为点数，`data-points` 是该层各行 `count` 之和）。

退出码：`0` 成功；`1` 输入不可用（文件缺失、JSON 解析失败、`t` 乱序或重复、`from > to`）；`2` 用法错误；非 0 时不写输出文件。

## 5. 性能与验收口径

只用标准库、单进程单线程。写入百万点 ≤ 20 秒；本仓库样例单条查询 ≤ 0.5 秒、整个 `samples/queries/` ≤ 2 秒；评测规模（百万级点、水位 400 天）单条 ≤ 2 秒、`render` ≤ 5 秒、峰值内存 ≤ 512 MB。存储上界：层 L 的条目数 ≤「保留期 ÷ 桶宽」+ 1，粗层合计 ≤ 23443 条，加上原始层窗口内的点数就是全部；写入一个点最多新增 4 条（1 条原始 + 3 个桶，桶在只更新），多留三层不会让占用随原始点数翻倍。

1. `query` 的 stdout 与 `samples/expected/` 同名文件逐字节相同（11 组，同名 `.jsonl` ↔ `.tsv`）；给了 `--out` 时也逐字节相同。
2. 交界处：`t = W-259200` 归原始层、`t = W-259201` 归分钟级最后一个桶；被端点切掉一半的桶不输出；与第 6 节几组交界样例的期望一致。
3. 超出保留期：`W-400d` 之前无覆盖，`beyond-retention`、`sparse-gap` 都是 0 字节，`beyond-partial` 只剩 `[W-400d, W-400d+86399]` 这一个天桶。
4. 写入时降采样：查询阶段不重算粗层；`stats` 满足上界，`layers.html` 的 `data-buckets` 与同一份统计一致。
5. 同一份输入连跑两次，stdout、`--out` 文件与 `layers.html` 逐字节一致：不要写时间戳、绝对路径、主机名；`python -m unittest` 能跑通，测试只读 `samples/`。

## 6. 样例说明

查询文件与期望结果同名一一对应（`.jsonl` ↔ `.tsv`）；`samples/notes.md` 是现场记录。

| 查询文件 | 层 | 行数 | 场景 |
| --- | --- | --- | --- |
| `raw-tail.jsonl` | raw | 21 | 原始窗口内逐点输出 |
| `raw-minute-seam.jsonl` | minute+raw | 16 | 3 天交界两侧 |
| `seam-single-second.jsonl` | raw | 1 | 交界那一秒，单点区间 |
| `clipped-buckets.jsonl` | minute | 2 | 端点切进桶里，半截桶丢弃 |
| `minute-hour-seam.jsonl` | hour+minute | 4 | 14 天交界两侧 |
| `hour-day-seam.jsonl` | day+hour | 3 | 120 天交界两侧 |
| `four-layers.jsonl` | day+hour+minute+raw | 112 | 满 400 天，四层拼接 |
| `day-window.jsonl` | day | 2 | 天桶每桶三条点，聚合值各异 |
| `sparse-gap.jsonl` | — | 0 | 区间落在层里但这段没有点 |
| `beyond-retention.jsonl` | — | 0 | 整段早于 400 天 |
| `beyond-partial.jsonl` | day | 1 | 前段留空，只回一个天桶 |

## 7. 待补的文档

- 百万级与隐藏用例在评测侧生成，不入库；参数见 `samples/points/scale.md`。
- 页面配色、刻度与文案自定，4.3 的元素与属性不能少；帮助文本与错误文案也自定。
- 落盘格式、压缩、多序列与跨进程共享留待后续。
