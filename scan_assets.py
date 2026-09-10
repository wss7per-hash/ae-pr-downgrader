"""工程体检（asset audit）。

不安装 After Effects / Premiere Pro，从工程文件里提取「协作交付与降级时
最容易丢东西」的依赖信息：

  - 版本信息（复用 aep_core / prproj_core 的权威识别）
  - 第三方插件 / AE 内置效果（ADBE matchName）
  - 字体
  - 素材路径
  - 表达式使用情况

实现方式：对二进制工程做**字符串提取**（ASCII + UTF-16LE 宽字符），
再做启发式分类。这是「尽力提取」，不是工程软件的权威解析——
文件名/效果名可能藏在压缩块或专有编码里，遗漏是正常的。
准确结论永远以「目标版本软件实机打开」为准。

CS6 之后的 .prproj 是 gzip 压缩的 XML，解压后直接文本搜索，覆盖最好。
.aep / .ffx 是 RIFX 二进制，靠字符串扫描，效果名/字体名多为 UTF-16LE。
.aepx / .ffx (XML 变体) 是纯文本，覆盖也好。
"""

import gzip
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 复用权威版本识别与降级
from aep_core import scan_bytes, ScanResult, convert_file  # noqa: F401
from prproj_core import scan_prproj_bytes, PrResult, label_of_number, convert_prproj_file  # noqa: F401


# --------------------------------------------------------------------------
# 字符串提取
# --------------------------------------------------------------------------

def extract_ascii_strings(data: bytes, min_len: int = 4) -> List[str]:
    """扫描连续可打印 ASCII（0x20-0x7e）序列。"""
    out: List[str] = []
    buf: List[str] = []
    for b in data:
        if 0x20 <= b < 0x7F:
            buf.append(chr(b))
        else:
            if len(buf) >= min_len:
                out.append("".join(buf))
            buf = []
    if len(buf) >= min_len:
        out.append("".join(buf))
    return out


def extract_wide_strings(data: bytes, min_len: int = 3) -> List[str]:
    """扫描 UTF-16LE 宽字符串：可打印 ASCII 字符后跟一个 0x00。

    AE 工程里的效果名、字体名、素材路径大量使用 UTF-16LE，单字节扫描会漏掉。
    """
    out: List[str] = []
    buf: List[str] = []
    i = 0
    n = len(data)
    while i < n - 1:
        c = data[i]
        nxt = data[i + 1]
        if 0x20 <= c < 0x7F and nxt == 0x00:
            buf.append(chr(c))
            i += 2
        else:
            if len(buf) >= min_len:
                out.append("".join(buf))
            buf = []
            i += 1
    if len(buf) >= min_len:
        out.append("".join(buf))
    return out


# --------------------------------------------------------------------------
# 分类规则
# --------------------------------------------------------------------------

# 表达式特征词（命中即说明工程用了表达式驱动）
EXPRESSION_TOKENS = [
    "thisComp", "thisLayer", "thisProperty", "thisProject",
    "wiggle(", "loopIn(", "loopOut(", "valueAtTime", "posterizeTime",
    "timeRemap", "sourceRectAtTime", "toWorld(", "toComp(", "fromWorld(",
    "effect(", "hasParent", "thisComp.layer(", "position.value",
    "linear(", "clamp(", "seedRandom", "random(",
]

# AE 内置效果 matchName（以 ADBE 开头，官方保留前缀）
ADBE_EFFECT_RE = re.compile(r"\bADBE [A-Za-z0-9 .\-/]+\b")

# 已知第三方插件厂商 / 产品关键词（保守匹配，命中即提示第三方依赖）。
# 这些是「疑似」——项目名里也可能出现这些词，请结合实机确认。
THIRD_PARTY_HINTS = [
    "Trapcode", "Red Giant", "Particular", "Form", "Mir", "Shine", "Starglow",
    "Video Copilot", "Element 3D", "Optical Flares", "Saber", "Twitch",
    "Boris", "BCC", "Continuum", "Mocha", "Sapphire", "Genarts",
    "Rowbytes", "Frischluft", "Depth of Field", "RE:Vision", "Twixtor",
    "Neat Video", "Knoll", "Unmult", "Digieffects", "Damage", "VC Reflect",
    "Real Grain", "Pixel Filth", "Sound Keys", "Looks", "Colorista",
    "Denoiser", "PlaneSpace", "Particular", "Lux", "Horizon",
]

# 素材路径模式：含盘符的绝对路径，或 3 段以上的类 Unix 路径
MEDIA_PATH_RE = re.compile(
    r"[A-Za-z]:\\[^\x00-\x1f\r\n]{4,}\.\w{2,4}"
    r"|/(?:[^/\x00-\x1f]{1,}/){2,}[^/\x00-\x1f]+\.\w{2,4}"
)

# 常见字体厂商 / 字体名特征（启发式，命中即列出）
FONT_HINTS = [
    "Adobe", "Source Han", "思源", "Microsoft", "SimHei", "SimSun", "黑体",
    "宋体", "楷体", "Arial", "Helvetica", "Times", "Courier", "Calibri",
    "Noto", "PingFang", "Hiragino", "STHeiti", "Heiti", "FangSong",
    "Myriad", "Minion", "Georgia", "Verdana", "Tahoma", "Roboto",
    "苹方", "华文", "文泉驿", "DIN", "Akzidenz",
]

# 路径里常见的媒体扩展名（用于过滤噪音路径）
MEDIA_EXT = {
    "png", "jpg", "jpeg", "psd", "ai", "tif", "tiff", "gif", "bmp", "exr",
    "mov", "mp4", "avi", "mxf", "wmv", "m4v", "r3d", "ari", "braw",
    "wav", "mp3", "aif", "aiff", "m4a", "wma", "flac", "ogg",
    "aep", "aepx", "prproj", "ppj", "eps", "pdf", "svg",
}


# --------------------------------------------------------------------------
# 报告结构
# --------------------------------------------------------------------------

@dataclass
class AssetReport:
    path: str = ""
    kind: str = "unknown"            # 'ae' | 'ffx' | 'aepx' | 'prproj' | 'unknown'
    version_label: str = ""          # 如 "2025" / "26.x" / "Version 43"
    version_raw: str = ""            # 原始版本字符串
    adobe_effects: List[str] = field(default_factory=list)
    third_party: List[str] = field(default_factory=list)
    fonts: List[str] = field(default_factory=list)
    media_paths: List[str] = field(default_factory=list)
    has_expression: bool = False
    expression_count: int = 0
    risks: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 分类辅助
# --------------------------------------------------------------------------

def _dedup_keep(items: List[str], limit: int = 60) -> List[str]:
    seen = set()
    out = []
    for it in items:
        it = it.strip()
        if not it or it in seen:
            continue
        seen.add(it)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def _classify_expressions(text: str) -> int:
    cnt = 0
    for tok in EXPRESSION_TOKENS:
        if tok in text:
            cnt += 1
    return cnt


def _classify_adobe_effects(strings: List[str]) -> List[str]:
    found = set()
    for s in strings:
        for m in ADBE_EFFECT_RE.findall(s):
            found.add(m.strip())
    return sorted(found)


def _classify_third_party(strings: List[str]) -> List[str]:
    hits = set()
    blob = "\n".join(strings)
    for hint in THIRD_PARTY_HINTS:
        if hint.lower() in blob.lower():
            hits.add(hint)
    return sorted(hits)


# 这些词出现在字符串里时，大概率是软件名/项目名/路径片段，不是字体名
FONT_IGNORE_TOKENS = [
    "Adobe Premiere Pro", "Premiere Pro", "After Effects", "Adobe Audition",
    "Adobe Media Encoder", "Photoshop", "Illustrator", "Audition",
    "Audio Previewer", "Audio Preview", "Plugin", "Plug-in",
]


def _classify_fonts(strings: List[str]) -> List[str]:
    found = set()
    blob = "\n".join(strings)
    lower_blob = blob.lower()
    for hint in FONT_HINTS:
        if hint.lower() not in lower_blob:
            continue
        # 避免 "Times" 命中 "Timestamp"、"Adobe" 命中 "Adobeful" 等连写词，
        # 同时允许 "Adobe Heiti Std" / "Times New Roman" 这种空格分隔的完整字体名。
        # (?![A-Za-z0-9]) 表示 hint 后不能直接跟字母数字；
        # (?:[ ]+[A-Za-z0-9]+)* 允许跟任意多组"空格+单词"。
        pattern = re.escape(hint) + r"(?![A-Za-z0-9])(?:[ ]+[A-Za-z0-9]+)*"
        for m in re.finditer(pattern, blob):
            token = m.group(0).strip()
            if not token:
                continue
            # 过滤明显非字体的软件名/路径片段（保留原始大小写做判定）
            if any(ign.lower() in token.lower() for ign in FONT_IGNORE_TOKENS):
                continue
            found.add(token)
    return sorted(found)


def _classify_media_paths(strings: List[str]) -> List[str]:
    found = set()
    for s in strings:
        for m in MEDIA_PATH_RE.findall(s):
            # 只保留媒体扩展名
            ext = m.rsplit(".", 1)[-1].lower() if "." in m else ""
            if ext in MEDIA_EXT:
                found.add(m.strip())
    return sorted(found)


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------

def _build_risks(rep: AssetReport) -> None:
    if rep.third_party:
        rep.risks.append(
            "含 %d 个疑似第三方插件（%s 等）：目标机器需安装相同版本插件，"
            "否则相关效果会丢失或被禁用。"
            % (len(rep.third_party), "、".join(rep.third_party[:3]))
        )
    if rep.has_expression:
        rep.risks.append(
            "含表达式（%d 处特征）。降级后旧版一般能保留表达式，"
            "但新版本才有的表达式函数/方法会报错。"
            % rep.expression_count
        )
    if rep.media_paths:
        rep.risks.append(
            "引用 %d 个素材路径。降级只改版本标记，素材本身不动；"
            "确认目标机器上这些路径仍可访问（或已重新链接）。"
            % len(rep.media_paths)
        )
    if rep.fonts:
        rep.risks.append(
            "使用 %d 种字体（%s 等）。目标机器缺字体时会被替换，版面走样。"
            % (len(rep.fonts), "、".join(rep.fonts[:3]))
        )
    if not rep.risks:
        rep.notes.append("未检测到明显的高风险依赖；但字符串扫描可能遗漏专有编码里的内容，"
                         "最终以目标版本软件实机打开为准。")


def scan_ae_family(data: bytes, path: str) -> AssetReport:
    """AE / FFX / AEPX：二进制字符串扫描 + 复用版本识别。"""
    r = None
    try:
        r = scan_bytes(data, path)
    except Exception as e:  # noqa: BLE001
        rep = AssetReport(path=path, kind="unknown")
        rep.notes.append("版本识别失败：%s" % e)
        return rep
    if r is None:
        r = ScanResult(path=path, ftype="ae")

    rep = AssetReport(
        path=path,
        kind=(path.rsplit(".", 1)[-1].lower() if "." in (path or "") else "ae"),
        version_label=r.src_version,
        version_raw=r.src_version,
    )
    if not r.ok:
        rep.notes.append("版本识别：%s" % r.error)
        # 版本识别失败不影响字符串体检，继续扫描

    strings = extract_ascii_strings(data) + extract_wide_strings(data)
    blob = "\n".join(strings)

    rep.adobe_effects = _classify_adobe_effects(strings)
    rep.third_party = _classify_third_party(strings)
    rep.fonts = _classify_fonts(strings)
    rep.media_paths = _classify_media_paths(strings)
    rep.expression_count = _classify_expressions(blob)
    rep.has_expression = rep.expression_count > 0

    _build_risks(rep)
    return rep


def scan_pr_family(data: bytes, path: str) -> AssetReport:
    """PR：gzip 解压后 XML 文本搜索 + 复用版本识别。"""
    rp = PrResult()
    try:
        rp = scan_prproj_bytes(data, path)
    except Exception as e:  # noqa: BLE001
        rep = AssetReport(path=path, kind="unknown")
        rep.notes.append("版本识别失败：%s" % e)
        return rep

    rep = AssetReport(
        path=path,
        kind="prproj",
        version_label=rp.src_label,
        version_raw="Version %d" % rp.src_number if rp.src_number else "",
    )
    if not rp.ok:
        rep.notes.append("版本识别：%s" % rp.error)
        return rep

    xml = rp.xml or ""
    # PR XML 文本搜索（已是 utf-8 字符串）
    strings = [xml]
    # 也做宽字符扫描（纯文本一般不会命中，但保险）
    blob = xml

    rep.adobe_effects = _classify_adobe_effects([xml])  # PR XML 里 ADBE 罕见
    rep.third_party = _classify_third_party([xml])
    # PR 的字体只从 <Font>/<FontPath> 标签提取，不对整段 XML 跑 hint 匹配，
    # 否则会误把 "Adobe Premiere Pro" 等软件名当成字体（见下方标签提取）。
    rep.fonts = []
    rep.media_paths = _classify_media_paths([xml])
    rep.expression_count = _classify_expressions(blob)
    rep.has_expression = rep.expression_count > 0

    # PR 特有：直接抓 XML 里的字体 / 路径标签（更准）
    for m in re.findall(r"<Font[^>]*>([^<]{1,40})</Font>", xml):
        if m.strip():
            rep.fonts.append(m.strip())
    for m in re.findall(r"<FontPath[^>]*>([^<]{1,200})</FontPath>", xml):
        fp = m.strip()
        if not fp:
            continue
        # 取文件名（去目录），再去掉字体扩展名，得到干净的字体名
        name = fp.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        name = re.sub(r"\.(ttf|otf|ttc|fon)$", "", name, flags=re.I)
        if name:
            rep.fonts.append(name)
    for tag in ("MediaPath", "FilePath", "ClipPath", "AudioPath"):
        for m in re.findall(r"<%s[^>]*>([^<]{1,260})</%s>" % (tag, tag), xml):
            if m.strip() and "." in m:
                rep.media_paths.append(m.strip())

    rep.fonts = _dedup_keep(rep.fonts)
    rep.media_paths = _dedup_keep(rep.media_paths)
    _build_risks(rep)
    return rep


def scan_assets_bytes(data: bytes, path: str = "") -> AssetReport:
    """统一入口：按扩展名 / 魔数分派。

    - .prproj 或无扩展名但 gzip 魔数 → PR
    - .aep / .ffx / .aepx 或 RIFX 魔数 → AE 家族
    - 其它 → 尽力扫描（按内容猜测）
    """
    ext = (path or "").lower().rsplit(".", 1)[-1] if "." in (path or "") else ""
    is_rifx = data[:4] == b"RIFX"
    is_gzip = len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B

    if ext == "prproj" or (not ext and is_gzip):
        return scan_pr_family(data, path)
    if ext in ("aep", "ffx", "aepx") or is_rifx:
        return scan_ae_family(data, path)

    # 未知：尽力而为——gzip 试 PR，否则字符串扫描
    if is_gzip:
        try:
            return scan_pr_family(data, path)
        except Exception:  # noqa: BLE001
            pass
    if is_rifx:
        return scan_ae_family(data, path)
    rep = AssetReport(path=path, kind="unknown")
    rep.notes.append("无法识别文件类型，已跳过（支持 .aep/.ffx/.aepx/.prproj）。")
    return rep


def scan_assets_file(path: str) -> AssetReport:
    with open(path, "rb") as f:
        data = f.read()
    return scan_assets_bytes(data, path)


def report_to_text(rep: AssetReport) -> str:
    """生成人类可读的体检报告（给 CLI / 调试用）。"""
    lines = []
    lines.append("工程体检：%s" % (rep.path or "(内存)"))
    lines.append("类型：%s　版本：%s" % (rep.kind, rep.version_label or "未知"))
    lines.append("-" * 40)
    lines.append("AE 内置效果：%d 个" % len(rep.adobe_effects))
    for e in rep.adobe_effects[:10]:
        lines.append("  · %s" % e)
    lines.append("第三方插件（疑似）：%d 个 -> %s"
                 % (len(rep.third_party), "、".join(rep.third_party) or "无"))
    lines.append("字体：%d 种 -> %s" % (len(rep.fonts), "、".join(rep.fonts[:8]) or "无"))
    lines.append("素材路径：%d 个" % len(rep.media_paths))
    for p in rep.media_paths[:8]:
        lines.append("  · %s" % p)
    lines.append("表达式：%s（%d 处特征）" % ("是" if rep.has_expression else "否",
                                          rep.expression_count))
    lines.append("-" * 40)
    if rep.risks:
        lines.append("风险提示：")
        for r in rep.risks:
            lines.append("  ! %s" % r)
    for n in rep.notes:
        lines.append("  · %s" % n)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 发包清单（依赖清单 / delivery manifest）
# --------------------------------------------------------------------------
# 把工程体检的结果进一步加工成「协作交付清单」：
#   - 逐条核对素材路径在本机是否找得到（绝对路径 / 相对工程目录 / 候选根目录）
#   - 汇总字体、第三方插件、AE 内置效果、表达式
#   - 一键产出 Markdown / JSON / CSV，或把能找到的文件打包成交付文件夹

import os
from datetime import datetime


@dataclass
class MediaEntry:
    path: str = ""                 # 工程里写死的原始素材路径
    exists: bool = False           # 本机能否找到
    found_at: str = ""             # 实际命中的本地文件（存在时填充）
    note: str = ""                 # 'absolute' | 'relative' | 'resolved' | 'missing'


@dataclass
class DeliverManifest:
    project_name: str = ""
    kind: str = "unknown"
    version_label: str = ""
    generated_at: str = ""
    media: List[MediaEntry] = field(default_factory=list)
    fonts: List[str] = field(default_factory=list)
    plugins: List[str] = field(default_factory=list)
    adobe_effects: List[str] = field(default_factory=list)
    has_expression: bool = False
    expression_count: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def media_missing(self) -> int:
        return sum(1 for m in self.media if not m.exists)


def _resolve_media_path(orig: str, project_path: str,
                        extra_roots: Optional[List[str]] = None) -> MediaEntry:
    """判定一个素材路径在本地是否存在，返回 MediaEntry。

    依次尝试：原样（绝对路径）→ 相对工程目录 → 各候选根目录下按文件名 / 相对路径。
    """
    candidates: List[str] = [orig]
    base = os.path.dirname(os.path.abspath(project_path)) if project_path else ""
    if base:
        candidates.append(os.path.join(base, orig))
    for r in (extra_roots or []):
        if not r:
            continue
        candidates.append(os.path.join(r, os.path.basename(orig)))
        if base:
            candidates.append(os.path.join(r, orig))
    for c in candidates:
        try:
            if os.path.isfile(c):
                return MediaEntry(
                    path=orig, exists=True, found_at=c,
                    note="absolute" if os.path.isabs(orig) else "relative")
        except OSError:
            continue
    return MediaEntry(path=orig, exists=False, note="missing")


def build_manifest(rep: AssetReport, project_path: str = "",
                   extra_roots: Optional[List[str]] = None) -> DeliverManifest:
    """根据体检报告 + 工程在磁盘上的位置，生成「发包清单」。"""
    base = os.path.dirname(os.path.abspath(project_path)) if project_path else ""
    roots = [r for r in ([base] + list(extra_roots or [])) if r]
    media = [_resolve_media_path(p, project_path, roots) for p in rep.media_paths]
    mf = DeliverManifest(
        project_name=(os.path.basename(project_path)
                      if project_path else (rep.path or "(内存)")),
        kind=rep.kind,
        version_label=rep.version_label,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        media=media,
        fonts=list(rep.fonts),
        plugins=list(rep.third_party),
        adobe_effects=list(rep.adobe_effects),
        has_expression=rep.has_expression,
        expression_count=rep.expression_count,
    )
    if mf.media_missing:
        mf.notes.append(
            "⚠ %d 个素材在本机找不到（可能用了相对链接、网络盘或未随工程拷贝），"
            "发包前请确认已放入交付包，否则协作方打开会缺素材。" % mf.media_missing)
    if rep.third_party:
        mf.notes.append("协作方电脑需安装相同版本第三方插件：%s 等。"
                        % "、".join(rep.third_party[:5]))
    if rep.fonts:
        mf.notes.append("协作方电脑需安装字体：%s 等，否则版面会被替换走样。"
                        % "、".join(rep.fonts[:5]))
    if rep.has_expression:
        mf.notes.append("工程用了表达式（%d 处特征），降到旧版后新版专属函数可能报错，"
                        "请协作方用目标版本实机确认。" % rep.expression_count)
    if not mf.notes:
        mf.notes.append("未检测到高风险依赖；但字符串扫描可能遗漏专有编码内容，"
                        "最终以目标版本软件实机打开为准。")
    return mf


def manifest_to_markdown(mf: DeliverManifest) -> str:
    L: List[str] = []
    L.append("# 发包清单 / Delivery Manifest")
    L.append("")
    L.append("- 工程：`%s`" % mf.project_name)
    L.append("- 类型：%s" % mf.kind)
    L.append("- 版本：%s" % (mf.version_label or "未知"))
    L.append("- 生成时间：%s" % mf.generated_at)
    L.append("")
    L.append("## 摘要")
    L.append("")
    L.append("| 项目 | 数量 |")
    L.append("| --- | ---: |")
    L.append("| 素材文件 | %d（缺失 %d）|" % (len(mf.media), mf.media_missing))
    L.append("| 字体 | %d |" % len(mf.fonts))
    L.append("| 第三方插件 | %d |" % len(mf.plugins))
    L.append("| AE 内置效果 | %d |" % len(mf.adobe_effects))
    L.append("| 表达式 | %s |"
             % ("是（%d 处）" % mf.expression_count if mf.has_expression else "否"))
    L.append("")
    L.append("## 素材清单")
    L.append("")
    if mf.media:
        for x in mf.media:
            mark = "✅" if x.exists else "❌"
            extra = "" if x.exists else "　（本机未找到）"
            L.append("- [%s] `%s`%s" % (mark, x.path, extra))
    else:
        L.append("（未检测到素材路径）")
    L.append("")
    L.append("## 字体")
    L.append("")
    L.append("、".join("`%s`" % f for f in mf.fonts) if mf.fonts else "（无）")
    L.append("")
    L.append("## 第三方插件（疑似）")
    L.append("")
    L.append("、".join("`%s`" % p for p in mf.plugins) if mf.plugins else "（无）")
    L.append("")
    if mf.adobe_effects:
        L.append("## AE 内置效果（ADBE）")
        L.append("")
        L.append("、".join("`%s`" % e for e in mf.adobe_effects))
        L.append("")
    if mf.has_expression:
        L.append("## 表达式")
        L.append("")
        L.append("本工程使用了表达式（%d 处特征），降级到旧版后新版专属函数可能报错。"
                 % mf.expression_count)
        L.append("")
    if mf.media_missing:
        L.append("## ⚠ 缺失素材（必须补齐才能完整交付）")
        L.append("")
        for x in mf.media:
            if not x.exists:
                L.append("- `%s`" % x.path)
        L.append("")
    L.append("## 给协作方的说明")
    L.append("")
    for n in mf.notes:
        L.append("- %s" % n)
    L.append("")
    L.append("> 本清单由 ae-pr-downgrader 根据工程文件字符串扫描生成，属「尽力提取」而非权威解析；")
    L.append("> 准确结论以目标版本 AE / PR 实机打开为准。")
    L.append("")
    return "\n".join(L)


def manifest_to_json(mf: DeliverManifest) -> str:
    import json
    obj = {
        "project": mf.project_name,
        "kind": mf.kind,
        "version_label": mf.version_label,
        "generated_at": mf.generated_at,
        "summary": {
            "media": len(mf.media),
            "media_missing": mf.media_missing,
            "fonts": len(mf.fonts),
            "plugins": len(mf.plugins),
            "adobe_effects": len(mf.adobe_effects),
            "has_expression": mf.has_expression,
            "expression_count": mf.expression_count,
        },
        "media": [
            {"path": x.path, "exists": x.exists,
             "found_at": x.found_at, "note": x.note}
            for x in mf.media
        ],
        "fonts": mf.fonts,
        "plugins": mf.plugins,
        "adobe_effects": mf.adobe_effects,
        "notes": mf.notes,
    }
    return json.dumps(obj, ensure_ascii=False, indent=2)


def manifest_to_csv(mf: DeliverManifest) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["类别", "名称", "状态", "路径"])
    for x in mf.media:
        w.writerow(["素材", x.path, "存在" if x.exists else "缺失", x.path])
    for f in mf.fonts:
        w.writerow(["字体", f, "", ""])
    for p in mf.plugins:
        w.writerow(["插件", p, "", ""])
    for e in mf.adobe_effects:
        w.writerow(["AE效果", e, "", ""])
    return buf.getvalue()


def _delivery_readme(mf: DeliverManifest) -> str:
    L = []
    L.append("交付说明 / Delivery README")
    L.append("=" * 40)
    L.append("")
    L.append("工程：%s（%s，版本 %s）" % (mf.project_name, mf.kind,
                                       mf.version_label or "未知"))
    L.append("生成时间：%s" % mf.generated_at)
    L.append("")
    L.append("【协作方打开前请确认】")
    L.append("1. 本文件夹已包含能找到的素材，放在 media/ 下；")
    L.append("   若 manifest 里标 ❌ 的素材缺失，请向发包方索要后放入 media/，")
    L.append("   并在工程里重新链接。")
    if mf.plugins:
        L.append("2. 工程用到第三方插件：%s 等，请在你的 AE / PR 安装相同版本，"
                "否则相关效果会丢失或被禁用。" % "、".join(mf.plugins[:5]))
    if mf.fonts:
        L.append("3. 工程用到字体：%s 等，请安装，否则版面会被替换走样。"
                % "、".join(mf.fonts[:5]))
    if mf.has_expression:
        L.append("4. 工程用了表达式，旧版软件里新版专属函数可能报错，请用目标版本实机核对。")
    L.append("")
    L.append("注：本工具只改版本标记让旧版愿意加载，不做真正的格式转换；")
    L.append("务必用目标版本的 AE / PR 实机打开确认。")
    L.append("")
    return "\n".join(L)


def package_project(project_path: str, out_dir: str, target=None,
                    dry_run: bool = False) -> dict:
    """组装一个可直接发给协作方的交付文件夹。

    - 把工程（可选降级到 target）复制到 out_dir
    - 把所有能找到的素材复制到 out_dir/media/
    - 写入 manifest.md / manifest.json / 交付说明.txt
    返回统计 dict（含 manifest 对象）。
    """
    import shutil
    if not os.path.isfile(project_path):
        raise FileNotFoundError(project_path)
    ext = project_path.lower().rsplit(".", 1)[-1]
    is_pr = ext == "prproj"
    proj_out = os.path.join(out_dir, os.path.basename(project_path))
    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)

    # 1) 准备要交付的工程文件（原样复制或降级）
    if target is not None:
        if is_pr:
            r = convert_prproj_file(project_path, proj_out, int(target),
                                    dry_run=dry_run)
        else:
            r = convert_file(project_path, proj_out, float(target),
                             dry_run=dry_run)
        if r.status == "failed":
            raise RuntimeError("工程降级失败：%s" % r.message)
    else:
        if not dry_run:
            shutil.copy2(project_path, proj_out)

    # 2) 扫描（用交付出去的那个文件），生成清单
    scan_src = proj_out if not dry_run else project_path
    rep = scan_assets_file(scan_src)
    mf = build_manifest(rep, scan_src,
                        extra_roots=[os.path.dirname(os.path.abspath(project_path))])

    # 3) 复制素材
    media_dir = os.path.join(out_dir, "media")
    copied = []
    if not dry_run:
        os.makedirs(media_dir, exist_ok=True)
        for x in mf.media:
            if x.exists:
                dst = os.path.join(media_dir, os.path.basename(x.path))
                if not os.path.exists(dst):
                    try:
                        shutil.copy2(x.found_at, dst)
                        copied.append(dst)
                    except Exception as e:  # noqa: BLE001
                        mf.notes.append("素材复制失败：%s -> %s" % (x.path, e))

    # 4) 写清单
    if not dry_run:
        with open(os.path.join(out_dir, "manifest.md"), "w",
                  encoding="utf-8") as f:
            f.write(manifest_to_markdown(mf))
        with open(os.path.join(out_dir, "manifest.json"), "w",
                  encoding="utf-8") as f:
            f.write(manifest_to_json(mf))
        with open(os.path.join(out_dir, "交付说明.txt"), "w",
                  encoding="utf-8") as f:
            f.write(_delivery_readme(mf))

    return {
        "out_dir": out_dir,
        "project_out": proj_out,
        "media_total": len(mf.media),
        "media_missing": mf.media_missing,
        "media_copied": len(copied),
        "manifest": mf,
    }
