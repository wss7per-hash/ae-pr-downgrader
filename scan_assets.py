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

# 复用权威版本识别
from aep_core import scan_bytes, ScanResult  # noqa: F401
from prproj_core import scan_prproj_bytes, PrResult, label_of_number  # noqa: F401


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


def _classify_fonts(strings: List[str]) -> List[str]:
    found = set()
    blob = "\n".join(strings)
    for hint in FONT_HINTS:
        if hint.lower() in blob.lower():
            # 提取包含该 hint 的相邻词，尽量给具体字体名
            for m in re.finditer(re.escape(hint) + r"[A-Za-z0-9 ]{0,24}", blob):
                token = m.group(0).strip()
                if token:
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
    rep.fonts = _classify_fonts([xml])
    rep.media_paths = _classify_media_paths([xml])
    rep.expression_count = _classify_expressions(blob)
    rep.has_expression = rep.expression_count > 0

    # PR 特有：直接抓 XML 里的字体 / 路径标签（更准）
    for m in re.findall(r"<Font[^>]*>([^<]{1,40})</Font>", xml):
        if m.strip():
            rep.fonts.append(m.strip())
    for m in re.findall(r"<FontPath[^>]*>([^<]{1,200})</FontPath>", xml):
        if m.strip():
            rep.fonts.append(m.strip())
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
