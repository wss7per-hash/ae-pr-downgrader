# -*- coding: utf-8 -*-
"""
prproj_core.py —— Premiere Pro 工程（.prproj）降级核心库

.prproj 是什么
--------------
CS6 之后，Premiere Pro 工程文件是 **gzip 压缩的 XML**：
    文件字节流 = gzip( XML 文本 )
解压后第 4 行左右长这样：

    <?xml version="1.0" encoding="UTF-8"?>
    <PremiereData Version="3">
      <Project ObjectRef="1"/>
      <Project ObjectID="1" ClassID="62ad66dd-0dcd-42da-a660-6d8fbde94876" Version="43">
        ...

Premiere 打开工程时**先读这一处 Version**，比自己能支持的数字大就弹
"此项目由更新版本的 Adobe Premiere Pro 创建" 并拒绝打开。
所以降级 = 解压 → 把这一处数字改小 → 重新 gzip。原工程内容一个字节都不动。

版本号体系
----------
这里的 Version 是 **工程格式号**，与软件版本号不是一回事。对照表来自
helmut4 官方文档（专业 MAM 工具）与 Just Solve the File Format Problem，
两者在 2018–2024 段完全一致：

    CC 2015=30  CC 2017=32  CC 2018=34  CC 2019=36  CC 2020=38
    2021=39  2022=40  2023=41  2024=42  2025=43  2026=45

另有次要版本：CC=26  CC 2014=27  CC 2015.1=29  CC 2015.4=31
CC 2017.1=33  CC 2018.1=35  CC 2019.1=37

⚠ 本机没有安装 Premiere Pro，以上均为**文献值，未在本机实机验证**。
   工具因此额外提供 Version=1 的"通用兼容"选项：任何 Premiere 都能打开
   （会提示转换工程），不依赖对照表是否准确，是最保险的兜底。

已知风险：Premiere Pro 2026 改用更稀疏的序列化，会省略旧版本期望存在的
字段；仅改版本号可能让旧版报"工程损坏"。检测到源为 2026 时会明确警告。
"""

from __future__ import annotations

import gzip
import io
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SUPPORTED_EXT = (".prproj",)

# [标签, 工程格式号, 是否本工具实测过]
PR_VERSION_TABLE: List[Tuple[str, int, bool]] = [
    ("CC (2013)",           26, False),
    ("CC 2014",             27, False),
    ("CC 2015.1",           29, False),
    ("CC 2015.2",           30, False),
    ("CC 2015.4",           31, False),
    ("CC 2017",             32, False),
    ("CC 2017.1",           33, False),
    ("CC 2018",             34, False),
    ("CC 2018.1",           35, False),
    ("CC 2019",             36, False),
    ("CC 2019.1",           37, False),
    ("CC 2020",             38, False),
    ("2021",                39, False),
    ("2022",                40, False),
    ("2023",                41, False),
    ("2024",                42, False),
    ("2025",                43, False),
    # 2026 = 45（44 未见公开样本，据 helmut4 文档直接取 45）
    ("2026",                45, False),
]

# 通用兜底：任何 Premiere 都能打开（会提示"转换工程"），不依赖对照表
PR_SAFE_ANY = 1
PR_SAFE_LABEL = "通用兼容"

# 2026 稀疏序列化风险提示
PR_2026_NUM = 45

BY_NUM: Dict[int, str] = {}
BY_LABEL: Dict[str, int] = {}
for _lab, _num, _v in PR_VERSION_TABLE:
    BY_NUM.setdefault(_num, _lab)
    BY_LABEL[_lab.lower()] = _num

MAX_NUM = max(n for _, n, _ in PR_VERSION_TABLE)


class PrprojError(Exception):
    pass


@dataclass
class PrHit:
    """一处需要改写的版本标记（XML 文本内的字符偏移）。"""
    offset: int          # 字符索引，指向 Version 值首字符
    length: int          # 字符数
    kind: str            # 'project_version'
    old_value: int
    new_value: int = 0
    note: str = ""


@dataclass
class PrResult:
    path: str = ""
    ftype: str = "prproj"
    ok: bool = True
    error: str = ""
    src_number: int = 0
    src_label: str = ""
    was_gzip: bool = True
    xml: str = ""
    encoding: str = "utf-8"
    size: int = 0
    hits: List[PrHit] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 版本标签
# --------------------------------------------------------------------------

def label_of_number(n: int) -> str:
    if n == PR_SAFE_ANY:
        return PR_SAFE_LABEL + "（Version=1）"
    lab = BY_NUM.get(n)
    if lab:
        return "Premiere Pro %s (Version %d)" % (lab, n)
    if n < min(BY_NUM):
        return "早于 CC 的工程 (Version %d)" % n
    return "未知的新版本 (Version %d)" % n


def short_label_of_number(n: int) -> str:
    if n == PR_SAFE_ANY:
        return PR_SAFE_LABEL
    return BY_NUM.get(n, "Version %d" % n)


# --------------------------------------------------------------------------
# gzip 解包 / 打包
# --------------------------------------------------------------------------

def is_gzip(data: bytes) -> bool:
    return len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B


def decode_project(data: bytes) -> Tuple[str, bool, str]:
    """
    返回 (xml 文本, 是否原本是 gzip, 编码名)。
    老工程（CS6 之前）可能是不压缩的纯 XML，这里一并兼容。
    """
    if is_gzip(data):
        raw = gzip.decompress(data)
        gz = True
    else:
        raw = data
        gz = False
    enc = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # 极端情况下工程里混入了非法 UTF-8：退回 latin-1，保证 1 字节 = 1 字符，
        # 这样重新编码回去字节完全一致，不会破坏内容。
        text = raw.decode("latin-1")
        enc = "latin-1"
    return text, gz, enc


def encode_project(text: str, was_gzip: bool, encoding: str = "utf-8") -> bytes:
    raw = text.encode(encoding)
    if not was_gzip:
        return raw
    buf = io.BytesIO()
    # mtime=0：同样的输入永远得到同样的输出，便于比对；Premiere 不读这个字段
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as f:
        f.write(raw)
    return buf.getvalue()


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------

# 只匹配 <Project ...> 标签（后面必须跟空白，避免命中 <ProjectViewState> 之类），
# 且必须带 Version="数字"。工程里 <Project ObjectRef="1"/> 没有 Version，会自然跳过。
PROJECT_TAG_RE = re.compile(r'<Project\s[^>]*?\bVersion\s*=\s*["\'](\d+)["\']')
# 数字在整段里的位置（用于算出字符偏移）
VERSION_VAL_RE = re.compile(r'\bVersion\s*=\s*["\'](\d+)["\']')

# 版本头一定在文件最前面，为了不吃掉几十 MB 的正则开销，只扫前 2 MB
SCAN_WINDOW = 2 * 1024 * 1024


def find_project_version(text: str) -> Optional[Tuple[int, int]]:
    """返回 (字符偏移, 版本号)，找不到返回 None。"""
    window = text[:SCAN_WINDOW]
    m = PROJECT_TAG_RE.search(window)
    if not m:
        return None
    vm = VERSION_VAL_RE.search(m.group(0))
    if not vm:
        return None
    # + len('Version="') 之前的部分：直接算出数字串的起点
    start_in_tag = vm.start(1)
    return m.start() + start_in_tag, int(vm.group(1))


def scan_prproj_bytes(data: bytes, path: str = "") -> PrResult:
    res = PrResult(path=path, size=len(data))
    try:
        text, gz, enc = decode_project(data)
    except Exception as e:
        res.ok = False
        res.error = "解压失败，不是有效的 gzip / Premiere 工程：%s" % e
        return res

    res.xml = text
    res.was_gzip = gz
    res.encoding = enc

    # 基本校验：确认这确实是 Premiere 的 XML，而不是随便一个 gzip 文件
    head = text[:4096]
    if "<Project" not in head and "PremiereData" not in head:
        res.ok = False
        res.error = ("解压成功但内容不像 Premiere 工程"
                     "（前 4KB 内既没有 <Project> 也没有 PremiereData）")
        return res

    found = find_project_version(text)
    if found is None:
        res.ok = False
        res.error = "未在 XML 中找到 <Project ... Version=\"N\"> 版本标记，已中止"
        return res

    off, num = found
    res.src_number = num
    res.src_label = short_label_of_number(num)
    if num >= PR_2026_NUM:
        res.extra["sparse_2026"] = True
    res.hits.append(PrHit(offset=off, length=len(str(num)), kind="project_version",
                          old_value=num, note="根 Project 元素 · Version"))
    return res


def apply_pr_patch(text: str, hits: List[PrHit]) -> str:
    """按偏移从后往前替换，避免前面的长度变化影响后面的偏移。"""
    out = text
    for h in sorted(hits, key=lambda x: -x.offset):
        new = str(h.new_value)
        out = out[:h.offset] + new + out[h.offset + h.length:]
    return out


def compute_pr_new_values(res: PrResult, target: int) -> None:
    for h in res.hits:
        h.new_value = target


# --------------------------------------------------------------------------
# 内存级转换 + 文件级转换
# --------------------------------------------------------------------------

def convert_prproj_bytes(data: bytes, target: int, path: str = "") -> Tuple[bytes, PrResult]:
    """
    把内存里的 .prproj 降到 target。返回 (新字节, 扫描结果)。
    会做往返校验：重新解压解析，确认读出来的就是目标版本。
    """
    res = scan_prproj_bytes(data, path)
    if not res.ok:
        raise PrprojError(res.error)
    if res.src_number == target:
        raise PrprojError("源版本已经是 Version %d，无需改写" % target)

    compute_pr_new_values(res, target)
    new_xml = apply_pr_patch(res.xml, res.hits)
    new_data = encode_project(new_xml, res.was_gzip, res.encoding)

    chk = scan_prproj_bytes(new_data, path)
    if not chk.ok:
        raise PrprojError("往返校验失败：改写后的文件无法解析（%s）" % chk.error)
    if chk.src_number != target:
        raise PrprojError("往返校验失败：期望 Version %d，实际读出 %d"
                          % (target, chk.src_number))
    return new_data, res


def convert_prproj_file(src: str, dst: str, target: int,
                        dry_run: bool = False, overwrite: bool = False):
    """
    转换单个文件，返回 aep_core.ConvertResult（与 AE 侧保持同一种结果结构）。
    原文件永远不动。
    """
    from aep_core import ConvertResult   # 延迟导入，避免循环依赖

    r = ConvertResult(path=src, dst_version=short_label_of_number(target))
    try:
        with open(src, "rb") as f:
            data = f.read()
    except Exception as e:
        r.status = "failed"
        r.message = "读取失败：%s" % e
        return r

    try:
        res = scan_prproj_bytes(data, src)
    except Exception as e:
        r.status = "failed"
        r.message = "扫描失败：%s" % e
        return r

    if not res.ok:
        r.status = "failed"
        r.message = res.error
        return r

    r.src_version = "Version %d (%s)" % (res.src_number, res.src_label)
    notes: List[str] = []
    if res.extra.get("sparse_2026"):
        notes.append("⚠ 源为 Premiere 2026，其稀疏序列化可能让旧版报工程损坏，请务必实机确认")
    if notes:
        r.message = " ".join(notes)

    # 源版本不高于目标 -> 原样复制
    if res.src_number <= target:
        r.status = "already_lower"
        r.message = (r.message + " " if r.message else "") + \
                    "源 Version %d 不高于目标 %d，原样复制" % (res.src_number, target)
        if not dry_run:
            try:
                _ensure_dir(os.path.dirname(dst))
                with open(dst, "wb") as f:
                    f.write(data)
                r.out_path = dst
                r.ok = True
            except Exception as e:
                r.status = "failed"
                r.message = "复制失败：%s" % e
        else:
            r.ok = True
        return r

    try:
        new_data, res2 = convert_prproj_bytes(data, target, src)
    except PrprojError as e:
        r.status = "failed"
        r.message = str(e)
        return r

    r.hits = len(res2.hits)
    r.verify = "OK"

    if not dry_run:
        try:
            _ensure_dir(os.path.dirname(dst))
            if os.path.abspath(src) == os.path.abspath(dst) and not overwrite:
                raise IOError("源与目标相同，拒绝原地覆盖")
            with open(dst, "wb") as f:
                f.write(new_data)
            r.out_path = dst
        except Exception as e:
            r.status = "failed"
            r.message = "写入失败：%s" % e
            return r

    r.status = "converted"
    r.ok = True
    if not r.message:
        r.message = "Version %d → %d" % (res.src_number, target)
    return r


def _ensure_dir(d: str) -> None:
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def risk_level(src: int, dst: int) -> Tuple[str, str]:
    """返回 (等级, 说明)。"""
    span = src - dst
    if span <= 0:
        return ("无需处理", "源版本不高于目标")
    if span <= 1:
        return ("推荐", "只回退 1 个版本，风险最低")
    if span <= 3:
        return ("实验性", "回退 %d 个版本，新特性可能丢失" % span)
    return ("高风险", "回退 %d 个版本，跨度过大会丢失大量新特性" % span)


# --------------------------------------------------------------------------
# 目标版本解析
# --------------------------------------------------------------------------

PR_ALIASES: Dict[str, int] = {
    "any": PR_SAFE_ANY, "1": PR_SAFE_ANY, "safe": PR_SAFE_ANY,
    "通用": PR_SAFE_ANY, "通用兼容": PR_SAFE_ANY,
    "cc2015": 30, "2015": 30,
    "cc2017": 32, "2017": 32,
    "cc2018": 34, "2018": 34,
    "cc2019": 36, "2019": 36,
    "cc2020": 38, "2020": 38,
    "2021": 39, "2022": 40, "2023": 41, "2024": 42, "2025": 43, "2026": 45,
}


def resolve_pr_target(s: str) -> int:
    if isinstance(s, int):
        return s
    k = str(s).strip().lower().replace("premiere", "").replace("pro", "").replace("pr", "").strip()
    k = k.strip(" v")
    if k in PR_ALIASES:
        return PR_ALIASES[k]
    if k.isdigit():
        n = int(k)
        if n in BY_NUM or n == PR_SAFE_ANY:
            return n
        raise ValueError("Version %d 不在对照表中（可用范围 %d–%d，或 any）"
                         % (n, min(BY_NUM), MAX_NUM))
    raise ValueError("无法识别的目标版本：%s（可用：2024 / 2023 / 2022 / 2021 / any）" % s)
