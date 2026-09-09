# AEP / PRPROJ 降级器

不安装 After Effects / Premiere Pro，也能把 `.aep` / `.aepx` 工程、`.ffx` 预设
和 **`.prproj` 工程**降到旧版本。

> **在线版**：https://wss7per-hash.github.io/aep-downgrader/ （GitHub Pages，文件同样不出本机）

| 文件 | 用途 | 需要 Python |
|---|---|---|
| **`AEP-Downgrader.html`** | 网页版，双击用浏览器打开，拖文件进去就能用。**推荐** | 不需要 |
| `.github/workflows/pages.yml` | 自动把网页版部署到 GitHub Pages（每次 push 自动生成 `index.html`，仓库里不留副本） | — |
| `aep_core.py` | AE 核心库（解析 / 改写 / 校验），可被其他脚本 import | 需要 |
| `prproj_core.py` | **PR 核心库**（gzip 解包 / XML 改写 / 回包 / 校验） | 需要 |
| `aep_cli.py` | 命令行工具，**按扩展名自动分派 AE / PR**，适合批量、递归、挂自动化 | 需要 |
| `test_core.js` | 自检脚本（AE 基础能力），用真实样本验证核心算法 | 需要 Node |
| `test_html_v2.js` | 自检脚本（AE 26.x / AEPX），验证网页版核心逻辑 | 需要 Node |
| `test_v2.py` | 自检脚本（AE 26.x / AEPX），验证 Python 核心库 | 需要 |
| `test_prproj.js` | **PR 自检**（网页版核心逻辑：gzip / 版本改写 / 自动分派） | 需要 Node |
| `test_prproj.py` | **PR 自检**（Python 核心库 + 文件级 + 幂等性） | 需要 |

两个版本（HTML / Python）共用同一套算法，已验证**逐字节输出一致**。

### 两种格式，两套版本体系

| | After Effects | Premiere Pro |
|---|---|---|
| 扩展名 | `.aep` / `.aepx` / `.ffx` | `.prproj` |
| 容器 | RIFX（大端 RIFF）二进制 / XML | **gzip 压缩的 XML**（CS6 之后） |
| 版本标记 | `svap` + `head` 两处版本字 + 格式字节 | **只有一处**：`<Project ... Version="N">` |
| 改动量 | 数个字节（默认 5 字节） | 1 个数字 |
| 版本体系 | major 11.x–26.x | 工程格式号 26–45（**与 AE 不通用**） |

> ⚠ **AE 与 PR 的版本号互不通用**，所以两者**不能混在一批里处理**。
> 命令行混放时会各走各的路径；网页版检测到混合会**禁用转换**并提示分开处理。

---

## 一、网页版（推荐，零安装）

**在线版**：https://wss7per-hash.github.io/aep-downgrader/

**本地版**：双击 `AEP-Downgrader.html` → 拖入文件 → 选目标版本 → 开始转换 → 下载。

在线版由 GitHub Actions 在每次 push 时自动从 `AEP-Downgrader.html` 生成，两者永远同一份内容；
即便如此，涉及工程文件仍建议用本地版，少一次网络往返。

- 纯前端，断网也能用，**文件不会上传到任何地方**
- 支持多选文件、整个文件夹（含子目录）
- **自动识别 `.aep` / `.ffx` / `.aepx` / `.prproj`**，目标版本下拉按文件类型自动切换
- 自动识别源版本，只列出比它低的目标版本
- 外推识别的版本（如 26.x）会在列表里标注「⚠推断」，体检面板给出提示
- 结果可单个下载，也可打包成 ZIP
- 内置「体检」面板，直接看到要改的是哪几个字节（AE）或哪一行 XML（PR）

> 网页版处理 `.prproj` 需要浏览器的 `DecompressionStream` / `CompressionStream`（gzip）：
> **Chrome / Edge 80+、Safari 16.4+、Firefox 113+**。不支持的浏览器会给出明确提示，
> 此时请用命令行版。

## 二、命令行版

**按扩展名自动分派**，`.prproj` 走 PR 路径，其余走 AE 路径，用法完全一致：

```bash
# 查看文件版本信息（不改动文件）—— AE 和 PR 都可以
python aep_cli.py info "D:\proj\demo.aep"
python aep_cli.py info "D:\proj\demo.prproj"

# 单个文件降到 AE 2024，输出到源目录旁的 AE24_0 子目录
python aep_cli.py conv "D:\proj\demo.aep" --to 2024

# PR 工程降到 Premiere 2024（输出子目录标签是 PR_V42）
python aep_cli.py conv "D:\proj\demo.prproj" --to 2024

# PR 工程降到「通用兼容」（Version=1，任何 Premiere 都能打开，最保险）
python aep_cli.py conv "D:\proj\demo.prproj" --to any

# 整个目录递归处理，输出到指定目录，保持目录结构
# （AE 与 PR 混放时会各自按自己的目标转换）
python aep_cli.py conv "D:\proj" --to 2023 --out "D:\out"

# 试运行，只看会改什么，不写盘
python aep_cli.py conv "D:\proj" --to 2024 --dry-run

# 就地覆盖（会先自动备份成 .bak）
python aep_cli.py conv "D:\proj" --to 2024 --in-place

# 列出所有支持的目标版本（AE 表 + PR 表一起打印）
python aep_cli.py versions
```

目标版本可以写 `2024` / `cs6` / `cc2018` / `22` 等，也接受纯数字 major（如 `24.0`）；
PR 额外支持 **`any`**（或 `1`），即通用兼容兜底。

参数：`--out` 输出目录 · `--in-place` 就地 · `--flat` 不保留子目录 ·
`--aggressive` 激进模式（**仅对 AE 生效**）· `--dry-run` 试运行 · `--no-recursive` 不递归

**原文件永远不动**，输出的是副本。

---

## 三、原理 · After Effects（`.aep` / `.aepx` / `.ffx`）

AEP / FFX 都是 **RIFX**（大端 RIFF）容器。AE 打开文件时先读工程头里的版本号，
**高于自身就直接拒载**，根本不检查内容。所以"降级"就是改写版本标记，让旧版 AE 愿意解析。

### `.aep` 的版本头在哪里

```
RIFX  size  "Egg!"  ─┬─ svap chunk  →  数据区 4 字节 = 版本字
                     ├─ head chunk  →  数据区 20 字节：
                     │      +0  00
                     │      +1  格式字节   ← 决定 AE 大版本（25.x = 0x60）
                     │      +2  00
                     │      +3  子版本
                     │      +4  版本字（u32be，与 svap 内相同）
                     └─ nhed / nnhd …
```

**注意：版本字存了两份**（`svap` 和 `head`），两处都要改。
网上流传的"从偏移 20 开始改"是十六进制编辑器里的 `0x20`（十进制 32），
且不同版本有没有 `svap` 块会导致 `head` 的位置不一样 —— 所以本工具**走 RIFX 解析而不是硬编码偏移**。

### `.ffx` 的版本头

```
RIFX  size  "FaFX"  ── head chunk → 数据区 16 字节：
                          +0   03（常量）
                          +4   格式字节（u32be，与 AEP 同一套编码）
                          +8   子版本
                          +12  无关数据
```

### `.aepx` 的版本头（XML）

AEPX 把 RIFX 的**每个 chunk 序列化成一个 XML 元素**，二进制数据区放进 `bdata` 属性
（连续的小写十六进制串）。真实样本：

```xml
<svap bdata="07789645"/>                                      ← 版本字（4 字节）
<head bdata="005c000e07789645800000000000000c00000019"/>       ← 20 字节，与 .aep 的 head 块一致
```

所以 `head` 的 `bdata` 布局与 `.aep` **完全相同**：

```
hex[0:2]  = 00
hex[2:4]  = 格式字节      ← 决定 AE 大版本
hex[4:6]  = 00
hex[6:8]  = 子版本
hex[8:16] = 版本字        ← 与 svap 内相同，两处都要改
```

自校验：把样本里的 `07789645` 按位域解码 = `15.1.2 build 69 Macintosh`，
和 `hex[2:4]` 的 `5c`（0x5C = CC 2018 = 15.x）完全自洽。

> 实现要点：AEPX 按 **latin-1** 解码（1 字节 = 1 字符），这样命中的偏移可直接用于字节级
> 补丁。若按 UTF-8 解码，中文等 3 字节字符会让字符偏移与字节偏移错位 —— 这是个坑。

### 版本字的位域结构

```
bit 0-7    build 号
bit 8      未用
bit 9-10   release 标志，正式版 = 0b11，beta = 0b00
bit 11-14  patch
bit 15-18  minor
bit 19-21  major 低 3 位
bit 22-25  平台    Windows=12 / Macintosh=13 / MacARM64=14
bit 26-31  major 高 6 位
major = (高 6 位 << 3) | 低 3 位
```

### 版本对照表

| 版本 | major | 格式字节 | 验证方式 |
|---|---|---|---|
| CS6 | 11.x | `0x51` | ✓ 官方预设 XMP |
| CC | 12.x | `0x56` | 按规律推定 |
| CC 2014 | 13.x | `0x57` | ✓ 官方预设 XMP |
| CC 2015 | 13.5 | `0x57` | ✓ 官方预设 XMP |
| CC 2017 | 14.x | `0x58` | ✓ 官方预设 XMP |
| CC 2018 | 15.x | `0x5C` | ✓ 官方预设 XMP |
| CC 2019 | 16.x | `0x5D` | 按规律推定 |
| 2020 / 2021 / 2022 | 17–22.x | `0x5D` | 按规律推定 |
| 2023 | 23.x | `0x5E` | ✓ 官方预设 XMP |
| 2024 | 24.x | `0x5F` | ✓ 官方预设 XMP |
| 2025 | 25.x | `0x60` | ✓ AE 2025 自带 `secret.aep` |
| **2026** | **26.x** | **`0x61`** | ⚠ **线性规律外推**（详见下节） |

### 这张表是怎么验证的（不是猜的）

1. **Adobe 官方预设库交叉验证**：AE 2025 安装目录下有 705 个 `.ffx`，
   每个文件尾部都内嵌 XMP，记录了 `CreatorTool`（如 `Adobe After Effects CC 2017 (Macintosh)`）。
   把这些 CreatorTool 与文件头的格式字节逐一比对，得到 `0x51=CS6` / `0x57=CC2014` /
   `0x58=CC2017` / `0x5C=CC2018` / `0x5E=2023` / `0x5F=2024` 的确定映射。
2. **AE 2025 真实工程验证**：`Support Files/Required/secret.aep` 是 AE 2025 保存的工程，
   格式字节 `0x60`，版本字 `0x0F08062F` 按位域解码 = `25.0.0 build 47 Windows`，完全吻合。
3. **26.x = 0x61 是外推值，但带双保险**：本机只装到 AE 2025，没有 2026 的样本。
   22.x–25.x 这四个**连续**大版本实测满足 `格式字节 = major + 71`（22→0x5D、23→0x5E、
   24→0x5F、25→0x60），据此外推 26.x = `0x61`。

   但工具不会盲信这个值 —— **外推值必须与文件内版本字解码出的 major 严格一致才会被采信**：

   | 格式字节 | 版本字解码 | 结果 |
   |---|---|---|
   | `0x61` | 26.x | ✅ 采信，界面标注「⚠推断」 |
   | `0x61` | 25.x | ❌ 拒绝，提示"两者不一致" |
   | `0x62` | 27.x | ✅ 按同一规律采信（未来版本自动兼容） |
   | `0x62` | 25.x | ❌ 拒绝 |

   两头对得上才动手，对不上就报错退出，一个字节都不改。

### 改写策略：最小改动

默认**只替换 major 位域**，平台、minor、patch、build、release 标志全部原样保留。
改动越少越不容易出错。实测 25.0.0 → 23.x 只动了 **5 个字节**（偏移 20/21/33/36/37），
文件长度完全不变。

`--aggressive` / 激进模式会把 minor / patch / build 一并归零。

---

## 四、原理 · Premiere Pro（`.prproj`）

**比 AE 简单得多**：CS6 之后的 `.prproj` 就是 **gzip 压缩的 XML**。
降级 = 解压 → 改一个数字 → 重新 gzip。

```
demo.prproj
  └─ gzip ──> <PremiereData Version="3">
                 <Project ObjectID="1" ClassID="62ad66dd-…" Version="43">   ← 就是这里
                   <Name>…</Name>
                   <ProjectViewState Version="7" ObjectRef="2"/>           ← 不是这里
                   …
```

Premiere 打开工程时先读根 `<Project>` 上的 `Version` 属性，
**比自身支持的大就弹「此项目由更新版本的 Adobe Premiere Pro 创建」并拒载**，内容一概不查。
所以改写这**一个数字**就够了。

### 精确定位的两个坑

1. **必须锚定 `<Project` 标签本身**。文件里还有 `<ProjectViewState Version="7">` 等
   一大堆带 `Version` 的元素，粗糙地替换第一个 `Version="…"` 会改错地方。
   用的正则是 `<Project\s[^>]*?\bVersion\s*=\s*["'](\d+)["']` —— 标签名后必须有空白，
   于是 `<ProjectViewState …>` 天然不匹配，`<Project ObjectRef="1"/>`（无 Version）也会自然跳过。
2. **从后往前替换**。命中可能有多个，正向替换会让后续偏移错位。

### 输出是否重新压缩

保持与源文件一致：

| 源文件 | 输出 |
|---|---|
| gzip 压缩 | 重新 gzip（`mtime=0`，**幂等**：同样输入永远同样输出字节） |
| 纯 XML（未压缩） | 纯 XML，不压缩 |

Premiere 两种都能读，所以照原样还回去最安全。

### `.prproj` 版本对照表（工程格式号）

| 版本 | Version | | 版本 | Version |
|---|---|---|---|---|
| CC (2013) | 26 | | 2020 | 38 |
| CC 2014 | 27 | | 2021 | 39 |
| CC 2015.1 | 29 | | 2022 | 40 |
| CC 2015.2 | 30 | | 2023 | 41 |
| CC 2015.4 | 31 | | 2024 | 42 |
| CC 2017 | 32 | | **2025** | **43** |
| CC 2017.1 | 33 | | **2026** | **45** ⚠ 见下 |
| CC 2018 | 34 | | | |
| CC 2018.1 | 35 | | | |
| CC 2019 | 36 | | | |
| CC 2019.1 | 37 | | | |

> 注意这是**工程格式号**，不是软件版本号 —— 两者不相等（2025 软件 = Version 43）。
> 2026 直接跳到 45（没有 44），这是 Premiere 官方的编号跳变，不是笔误。

**来源**：[helmut4 官方文档](https://docs.helmut4.io/) 的 Premiere 工程版本表
与 [Just Solve the File Format Problem](http://fileformats.archiveteam.org/wiki/Premiere_Project)
交叉比对，两者在 2018–2024 区间完全一致。

### 不放心？用「通用兼容 Version=1」

上面的年份 → Version 映射**全部来自文献，本机没有安装 Premiere Pro，没有实机验证过**。
因此提供一个**不依赖对照表**的兜底选项：

```
--to any    （网页版里是列表最后一项「通用兼容 Version=1」）
```

任何版本的 Premiere 都能打开 `Version=1` 的工程（会提示"转换工程"后打开）。
**成功率最高**，代价是 Premiere 会把工程当作旧格式做一次转换。
如果你的 Premiere 版本不在对照表里，或降级后打不开 —— 选它。

### Premiere 2026（Version 45）的额外风险

2026 的工程采用**稀疏序列化**：会省略旧版本期望存在的字段。
只把 `45` 改成 `42` 而不补回这些字段，旧版 Premiere 可能报「工程损坏」。

工具检测到 `Version >= 45` 时会打 `sparse_2026` 标记并给出警告。
这种情况**强烈建议直接用 `any`（Version=1）**，让 Premiere 自己走完整的转换流程。

---

## 五、必须知道的局限

**这是"改版本标记"，不是真正的格式转换。** 软件愿意打开 ≠ 内容完整。

- 目标版本不支持的**新效果、新属性、表达式、第三方插件**仍会丢失或报错
- Adobe 官方的「另存为低版本」只支持**回退 2 个大版本**，跨度越大风险越高
  （工具会按跨度标注：推荐 / 实验性 / 高风险）
- **26.x 是外推值**：本机无样本，靠 22.x–25.x 的线性规律推断，需与版本字交叉校验才采信
  （见上节）。真正的 26.x 工程建议先用副本试开
- **AEPX（XML 工程）**：已按真实样本实现了 `<head>` / `<svap>` 的 `bdata` 精确定位，
  但**没在 AE 生成的真实 `.aepx` 上做过端到端验证**（本机没有样本）。
  若 XML 里找不到标准的 `<head bdata>`，工具会**报错而不是乱改**。
  用之前请先做这一步验证：
  1. AE 里打开工程 → 文件 → 另存为 → 保存类型选 **XML 工程 (.aepx)**
  2. 用本工具降到目标版本
  3. 用目标版本 AE 打开降级后的 `.aepx`（或直接另存回 `.aep`）确认
- 老式 `FFX1` 预设（`06 00 00 00` 开头）不支持，会自动跳过
- 转换后用**目标版本的 AE 实机打开确认**，这是唯一可靠的验证

**Premiere Pro（`.prproj`）** 的额外说明：

- **本机未安装 Premiere Pro，也没有真实 `.prproj` 样本**，
  全部验证基于**合成样本**（gzip / 纯 XML / 含中文 / Version=1 兜底 / 各类错误分支，共 115 项自检）。
  **请务必先用副本试开。**
- 年份 → Version 的映射是**文献值**（helmut4 + Just Solve 交叉比对），未经实机验证；
  不确定就选 `any`（Version=1）
- **2026（Version 45）是稀疏序列化**，仅改版本号可能让旧版报「工程损坏」→ 建议用 `any`
- 与 AE 一样：目标版本不支持的新功能（新效果、新格式、扩展）仍会丢失
- AE 与 PR **不能混在一批处理**（版本号体系不同），网页版会检测并提示

---

## 六、自检

```bash
# After Effects
node test_core.js        # 基础能力（位域 / AEP / FFX / ZIP / 版本边界）    39 项
node test_html_v2.js     # 网页版：26.x + AEPX                             26 项
python test_v2.py        # Python 版：26.x + AEPX + 回归                    48 项

# Premiere Pro
node test_prproj.js      # 网页版 PR 核心：gzip / 改写 / 自动分派 / 幂等    42 项
python test_prproj.py    # Python PR 核心 + 文件级 + 风险等级 + 幂等        73 项
```

AE 覆盖：位域解码与合成、AEP/FFX/AEPX 定位、字节级差异、中文偏移正确性、
外推版本的交叉校验（一致才采信 / 不一致拒绝）、无法确认时拒绝改写、ZIP 生成。

PR 覆盖：gzip 解包与回包、纯 XML 与压缩两种情况、`<Project>` 精确定位
（不误伤 `ProjectViewState`）、中文保留、2026 稀疏标记、目标解析（`2024`/`cc2018`/`any`/纯数字）、
风险等级、文件级转换（目录结构 / dry-run / 原文件不动 / 2026 警告）、幂等性、
错误分支（非 gzip / 非 PR XML / 缺 Version / 损坏 gzip 均**返回错误而非抛异常**）。

当前 **228 项全部通过**，且网页版与 Python 版**逐字节输出一致**。

---

## 七、典型场景

**把 AE 2025 的工程发给用 AE 2023 的同事**
```bash
python aep_cli.py conv "D:\工程\片头.aep" --to 2023
```
AE 2023 是官方跨度内（回退 2 版），成功率最高。

**整个预设库批量降级**
```bash
python aep_cli.py conv "D:\Presets" --to 2018 --out "D:\Presets_2018"
```
实测 Adobe 官方 705 个预设：改写 564 / 跳过 141 / 失败 0。
（跳过的是本来就低于目标版本的老预设）

**XML 工程（.aepx）**
```bash
python aep_cli.py info "D:\proj\demo.aepx"     # 先确认能识别、改的是哪几处
python aep_cli.py conv "D:\proj" --to 2023     # .aep 和 .aepx 可以混在一起批量处理
```

**不确定的时候，先试运行**
```bash
python aep_cli.py conv "D:\proj" --to 2024 --dry-run
```
只打印会改什么，不写盘。

**把 Premiere 2025 的工程发给用 Premiere 2023 的同事**
```bash
python aep_cli.py conv "D:\工程\婚礼片头.prproj" --to 2023
```
输出 `婚礼片头.prproj`（Version 43 → 41）到源目录旁的 `PR_V41` 子目录，原文件不动。

**对方装的是哪版 Premiere 不清楚**
```bash
python aep_cli.py conv "D:\工程\婚礼片头.prproj" --to any
```
降到 `Version=1`，任何 Premiere 都能打开（会提示转换工程），**成功率最高**。

**Premiere 2026 的工程**（⚠ 稀疏序列化，风险最高）
```bash
python aep_cli.py info "D:\工程\demo.prproj"     # 先看是不是 Version >= 45
python aep_cli.py conv "D:\工程\demo.prproj" --to any   # 建议直接用通用兼容
```

**AE 与 PR 混在一个目录里**
```bash
python aep_cli.py conv "D:\proj" --to 2024 --out "D:\out"
```
命令行会按扩展名各自分派：`.aep/.ffx/.aepx` 按 AE 的 2024，`.prproj` 按 PR 的 2024，
互不干扰。网页版遇到混合批次会**禁用转换并提示分开处理**。
