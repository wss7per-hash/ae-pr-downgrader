# -*- coding: utf-8 -*-
"""
aep_core.py —— AE 工程/预设降级核心库（不依赖 After Effects）

原理
----
AEP / FFX 都是 RIFX（大端 RIFF）容器。AE 打开文件时先读工程头里的"版本标记"，
若高于自身版本就直接拒载，并不检查内容。因此"降级"= 把版本标记改写为目标版本。

本库用 Adobe 官方素材实测验证过的关键偏移（见 README.md 的验证记录）：

  .aep : RIFX + size + "Egg!" + chunks...
         ├─ svap chunk : 数据区 4 字节 = 版本字（u32be）
         └─ head chunk : 数据区 20 字节
                          +0 : 00
                          +1 : 格式字节（决定 AE 大版本，25.x=0x60）
                          +2 : 00
                          +3 : 子版本
                          +4 : 版本字（u32be，与 svap 内相同）

  .ffx : RIFX + size + "FaFX" + chunks...
         └─ head chunk : 数据区 16 字节
                          +0 : 03（常量）
                          +4 : 格式字节（u32be，与 aep 同一套编码）
                          +8 : 子版本
                          +12: 无关数据

  .aepx: XML。RIFX 的每个 chunk 序列化成一个元素，二进制数据区放进 bdata 属性
         （连续小写十六进制串）。head 元素与 .aep 的 head 块布局完全一致：
             <svap bdata="<版本字 8 hex>"/>
             <head  bdata="00<格式字节 2>00<子版本 2><版本字 8>..."/>
         详见 scan_aepx 上方的样本与自校验说明。

版本字（u32be）位域（bit0 = 最低位）：
  bit 0-7    build 号
  bit 8      未用（0）
  bit 9-10   release 标志，正式版 = 0b11，beta = 0b00
  bit 11-14  patch
  bit 15-18  minor
  bit 19-21  major 低 3 位
  bit 22-25  平台   Win=12 / Mac=13 / MacARM64=14
  bit 26-31  major 高 6 位（取低 5 位有效）
  major = (majorHigh << 3) | majorLow

实测样本：AE 2025 保存的 secret.aep → 版本字 0x0F08062F = 25.0.0 build47 Windows

26.x 及更高版本
--------------
22.x 及以上格式字节满足 fmt = major + 71（22/23/24/25 四个连续大版本实测成立），
据此外推 26.x = 0x61。这类外推值只在"与文件内版本字解码出的 major 完全一致"时才采信，
两者互相印证才动手（classify_format_byte 的 'inferred' 分支），对不上就拒绝。
"""

from __future__ import annotations

import os
import re
import struct
import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

# --------------------------------------------------------------------------
# 版本表
# --------------------------------------------------------------------------

# 平台代号
OS_WIN = 12
OS_MAC = 13
OS_MAC_ARM = 14
OS_NAMES = {12: "Windows", 13: "Macintosh", 14: "Macintosh(ARM)"}

# 格式字节对照表。verified=True 表示已用 Adobe 官方素材的 XMP CreatorTool 交叉验证过。
#   label      major  格式字节  是否已实测验证
FORMAT_TABLE: List[Tuple[str, float, int, bool]] = [
    ("CS6 (11.x)",     11.0, 0x51, True),
    ("CC (12.x)",      12.0, 0x56, False),
    ("CC 2014 (13.x)", 13.0, 0x57, True),
    ("CC 2015 (13.5)", 13.5, 0x57, True),
    ("CC 2017 (14.x)", 14.0, 0x58, True),
    ("CC 2018 (15.x)", 15.0, 0x5C, True),
    ("CC 2019 (16.x)", 16.0, 0x5D, False),
    ("2020 (17.x)",    17.0, 0x5D, False),
    ("2021 (18.x)",    18.0, 0x5D, False),
    ("2022 (22.x)",    22.0, 0x5D, False),
    ("2023 (23.x)",    23.0, 0x5E, True),
    ("2024 (24.x)",    24.0, 0x5F, True),
    ("2025 (25.x)",    25.0, 0x60, True),
    ("2026 (26.x)",    26.0, 0x61, False),   # 见 LINEAR_BASE：由 22.x–25.x 的线性规律外推
]

# 据 major 反查格式字节（同一 major 可能多条，取第一条）
_BY_MAJOR: Dict[float, int] = {}
for _label, _m, _b, _v in FORMAT_TABLE:
    _BY_MAJOR.setdefault(_m, _b)

# 据格式字节反查 major（同一字节可能对应多个 major，取最小的那个作为"该字节代表的最低版本"）
_BY_BYTE: Dict[int, float] = {}
for _label, _m, _b, _v in FORMAT_TABLE:
    if _b not in _BY_BYTE or _m < _BY_BYTE[_b]:
        _BY_BYTE[_b] = _m

# 官方"另存为低版本"支持回退的大版本数
OFFICIAL_BACK_STEPS = 2


def format_byte_of(major: float) -> Optional[int]:
    """由 major 版本号取格式字节；未知返回 None。"""
    return _BY_MAJOR.get(major)


# 已知格式字节的取值范围，用于区分"更老"和"更新"的未知版本
_MIN_BYTE = min(b for _, _, b, _ in FORMAT_TABLE)   # 0x51 = CS6
_MAX_BYTE = max(b for _, _, b, _ in FORMAT_TABLE)   # 0x61 = 2026
_MAX_MAJOR = max(m for _, m, _, _ in FORMAT_TABLE)  # 26.0

# 早于 CS6 的老版本（0x44/0x45/0x46/0x49 等）：已低于所有可选目标，无需降级
OLDER_LABEL = "早于 CS6"

# 22.x 及以上，格式字节满足 fmt = major + 71，在 22.x/23.x/24.x/25.x 四个连续大版本上
# 均实测成立。据此可对更高版本（26.x、27.x…）做外推，但必须与"版本字解码出的 major"
# 完全一致才采信 —— 两者互相印证才动手，任何一个对不上就拒绝。
LINEAR_BASE = 71


# 靠线性规律外推出来的版本（26.x = 0x61）。这些条目即使已在表中，也必须与文件内
# 版本字解码出的 major 严格一致才采信 —— 否则说明外推值错了，直接拒绝。
EXTRAPOLATED = {26.0}


def classify_format_byte(b: int, word_major: Optional[int] = None) -> Tuple[str, Optional[float]]:
    """
    判定格式字节：
      ('known', major)    —— 已收录，实测或有权威来源
      ('older', None)     —— 比 CS6 更老，本来就低于任何目标版本，跳过即可
      ('inferred', major) —— 外推版本，但线性外推值与版本字解码结果自洽，采信（会提示）
      ('newer', None)     —— 无法确认（两端对不上或落在空隙里），拒绝处理
    """
    if b in _BY_BYTE:
        m = _BY_BYTE[b]
        if m in EXTRAPOLATED:
            if word_major is None:
                return "known", m        # 没有版本字可供校验（如 .ffx），退化为直接采信
            if int(word_major) == int(m):
                return "inferred", m
            return "newer", None
        return "known", m
    if b < _MIN_BYTE:
        return "older", None
    m = b - LINEAR_BASE
    if m > _MAX_MAJOR and word_major is not None and int(word_major) == m:
        return "inferred", float(m)
    return "newer", None


def infer_note(b: int, major: float) -> str:
    return ("格式字节 0x%02X 未经实测，按 22.x–25.x 的线性规律（fmt = major + %d）"
            "外推为 %d.x，且与文件内版本字解码结果一致，已采信。"
            % (b, LINEAR_BASE, int(major)))


def label_of_major(major: float) -> str:
    for label, m, b, v in FORMAT_TABLE:
        if abs(m - major) < 1e-6:
            return label
    return "未知版本 (%.1f)" % major


# --------------------------------------------------------------------------
# 版本字编解码
# --------------------------------------------------------------------------

def decode_word(v: int) -> Dict[str, Any]:
    """把 u32be 版本字解码为可读字段。"""
    major_high = (v >> 26) & 0x1F
    os_code = (v >> 22) & 0xF
    major_low = (v >> 19) & 0x7
    minor = (v >> 15) & 0xF
    patch = (v >> 11) & 0xF
    rel_bits = (v >> 9) & 0x3
    build = v & 0xFF
    major = (major_high << 3) | major_low
    return {
        "word": v,
        "major": major,
        "minor": minor,
        "patch": patch,
        "build": build,
        "os": os_code,
        "os_name": OS_NAMES.get(os_code, "未知平台(%d)" % os_code),
        "beta": rel_bits != 0b11,
        "version": "%d.%d.%d" % (major, minor, patch),
    }


def make_word(major: int, minor: int, patch: int, os_code: int, build: int,
              beta: bool = False) -> int:
    """按位域合成版本字。"""
    rel = 0b00 if beta else 0b11
    return (((major >> 3) & 0x1F) << 26) | ((os_code & 0xF) << 22) | \
           ((major & 0x7) << 19) | ((minor & 0xF) << 15) | ((patch & 0xF) << 11) | \
           (rel << 9) | (build & 0xFF)


def replace_major_in_word(v: int, new_major: int) -> int:
    """
    最小改动：只替换版本字里的 major 位域，保留平台/minor/patch/build/标志位。
    改得越少越不容易出错，这是默认策略。
    """
    keep = v & ~((0x1F << 26) | (0x7 << 19))
    return keep | (((new_major >> 3) & 0x1F) << 26) | ((new_major & 0x7) << 19)


def reset_word(v: int, new_major: int) -> int:
    """
    彻底模式：major 设为目标值，minor/patch/build 归零，保留平台与非 beta 标志。
    """
    d = decode_word(v)
    return make_word(new_major, 0, 0, d["os"], 1, beta=False)


# --------------------------------------------------------------------------
# RIFX 解析
# --------------------------------------------------------------------------

class RifxError(Exception):
    pass


@dataclass
class Chunk:
    name: str
    offset: int          # chunk 头起始偏移
    size: int            # 数据区长度
    data_offset: int     # 数据区起始偏移


def parse_top_chunks(buf: bytes, start: int, end: int, limit: int = 64) -> List[Chunk]:
    """解析某一层的 chunk 列表（RIFF 约定：数据区长度奇数时补 1 字节对齐）。"""
    out: List[Chunk] = []
    off = start
    while off + 8 <= end and len(out) < limit:
        name = buf[off:off + 4]
        size = struct.unpack(">I", buf[off + 4:off + 8])[0]
        data_off = off + 8
        if size > end - data_off:      # 尺寸异常，停止解析
            break
        try:
            nm = name.decode("ascii")
        except UnicodeDecodeError:
            break
        if not re.fullmatch(r"[ -~]{4}", nm):
            break
        out.append(Chunk(nm, off, size, data_off))
        off = data_off + size + (size & 1)
    return out


# --------------------------------------------------------------------------
# 扫描结果
# --------------------------------------------------------------------------

@dataclass
class VersionHit:
    """一处需要改写的版本标记。"""
    offset: int          # 文件内偏移
    length: int          # 字节数
    kind: str            # 'format_byte' | 'word'
    old_value: int
    new_value: int = 0
    note: str = ""


@dataclass
class ScanResult:
    path: str
    ftype: str                       # 'aep' | 'ffx' | 'aepx' | 'unknown'
    ok: bool = True
    error: str = ""
    src_version: str = ""
    src_major: float = 0.0
    src_format_byte: int = 0
    src_word: int = 0
    src_os: int = OS_WIN
    src_build: int = 0
    hits: List[VersionHit] = field(default_factory=list)
    size: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 扫描：AEP
# --------------------------------------------------------------------------

def scan_aep(buf: bytes, path: str = "") -> ScanResult:
    res = ScanResult(path=path, ftype="aep", size=len(buf))
    if len(buf) < 48 or buf[0:4] != b"RIFX":
        res.ok = False
        res.error = "不是 RIFX 容器（文件头不是 RIFX）"
        return res
    if buf[8:12] != b"Egg!":
        res.ok = False
        res.error = "RIFX 表单类型不是 'Egg!'，可能不是 AEP 工程"
        return res

    total = struct.unpack(">I", buf[4:8])[0]
    end = min(len(buf), 8 + total) if 8 + total <= len(buf) else len(buf)
    chunks = parse_top_chunks(buf, 12, end)
    by_name = {c.name: c for c in chunks}

    head = by_name.get("head")
    if head is None or head.size < 8:
        res.ok = False
        res.error = "未找到 head 块，无法定位版本标记"
        return res

    # head 数据区：[+0]=00 [+1]=格式字节 [+2]=00 [+3]=子版本 [+4..+7]=版本字
    fmt_off = head.data_offset + 1
    fmt_byte = buf[fmt_off]
    sub_off = head.data_offset + 3
    sub_ver = buf[sub_off]
    word_off = head.data_offset + 4
    word = struct.unpack(">I", buf[word_off:word_off + 4])[0]

    # 先解码版本字，再与格式字节交叉校验：两者一致才采信（26.x 及以上靠这条兜底）
    d = decode_word(word)
    kind, major = classify_format_byte(fmt_byte, d["major"])
    if kind == "newer":
        res.ok = False
        exp = _BY_BYTE.get(fmt_byte)
        if exp is not None and exp in EXTRAPOLATED:
            res.error = ("格式字节 0x%02X 按线性规律外推应为 %d.x，但文件内版本字解码为 %d.x，"
                         "两者不一致 —— 外推值不适用，已中止（未做任何改动）。"
                         % (fmt_byte, int(exp), d["major"]))
        else:
            res.error = ("无法识别的格式字节 0x%02X，且无法与版本字解码结果（%d.x）互相印证。"
                         "本工具不下发未经验证的猜测值，已中止。" % (fmt_byte, d["major"]))
        return res
    if kind == "older":
        res.src_format_byte = fmt_byte
        res.src_major = 0.0
        res.src_version = OLDER_LABEL
        res.extra["older_than_cs6"] = True
        return res
    if kind == "inferred":
        res.extra["inferred"] = True
        res.extra["infer_note"] = infer_note(fmt_byte, major)

    res.src_format_byte = fmt_byte
    res.src_word = word
    res.src_major = d["major"]
    res.src_os = d["os"]
    res.src_build = d["build"]
    res.src_version = d["version"]
    res.extra["sub_ver"] = sub_ver
    res.extra["head_offset"] = head.data_offset
    res.extra["platform"] = d["os_name"]

    # hit 1：head 内的格式字节
    res.hits.append(VersionHit(fmt_off, 1, "format_byte", fmt_byte,
                               note="head 块 · 格式字节"))
    # hit 2：head 内的版本字
    res.hits.append(VersionHit(word_off, 4, "word", word,
                               note="head 块 · 版本字"))

    # hit 3：svap 块（部分版本存在，内含同一份版本字）
    svap = by_name.get("svap")
    if svap is not None and svap.size >= 4:
        sv = struct.unpack(">I", buf[svap.data_offset:svap.data_offset + 4])[0]
        if sv == word:
            res.hits.append(VersionHit(svap.data_offset, 4, "word", word,
                                       note="svap 块 · 版本字"))
        else:
            res.extra["svap_mismatch"] = sv

    # 兜底：在 head / nhed / nnhd 数据区内再找一次与已知版本字相同的 4 字节，
    # 防止某些版本的布局差异导致漏改。
    known = set(h.offset for h in res.hits)
    for cname in ("head", "nhed", "nnhd"):
        c = by_name.get(cname)
        if c is None:
            continue
        seg = buf[c.data_offset:c.data_offset + c.size]
        for i in range(0, len(seg) - 3):
            if seg[i:i + 4] == struct.pack(">I", word):
                off = c.data_offset + i
                if off not in known:
                    known.add(off)
                    res.hits.append(VersionHit(off, 4, "word", word,
                                               note="%s 块 · 版本字(补充)" % cname))
    return res


# --------------------------------------------------------------------------
# 扫描：FFX
# --------------------------------------------------------------------------

def scan_ffx(buf: bytes, path: str = "") -> ScanResult:
    res = ScanResult(path=path, ftype="ffx", size=len(buf))
    if len(buf) < 48 or buf[0:4] != b"RIFX":
        res.ok = False
        res.error = "不是 RIFX 容器（文件头不是 RIFX）"
        return res
    if buf[8:12] != b"FaFX":
        res.ok = False
        res.error = "RIFX 表单类型不是 'FaFX'（老式 FFX1 预设不支持，已跳过）"
        return res

    total = struct.unpack(">I", buf[4:8])[0]
    end = min(len(buf), 8 + total) if 8 + total <= len(buf) else len(buf)
    chunks = parse_top_chunks(buf, 12, end)
    head = next((c for c in chunks if c.name == "head"), None)
    if head is None or head.size < 12:
        res.ok = False
        res.error = "未找到 head 块"
        return res

    fmt_off = head.data_offset + 4
    fmt_int = struct.unpack(">I", buf[fmt_off:fmt_off + 4])[0]
    sub_off = head.data_offset + 8
    sub_ver = struct.unpack(">I", buf[sub_off:sub_off + 4])[0] if head.size >= 12 else 0

    if fmt_int > 0xFF:
        res.ok = False
        res.error = "head 内格式字段异常（0x%08X），已中止" % fmt_int
        return res

    kind, major = classify_format_byte(fmt_int)
    if kind == "newer":
        res.ok = False
        res.error = ("无法识别的格式字节 0x%02X —— 比 AE 2025 更新（可能是更高版本保存的）。"
                     "本工具不下发未经验证的猜测值，已中止。" % fmt_int)
        return res
    if kind == "older":
        res.src_format_byte = fmt_int
        res.src_major = 0.0
        res.src_version = OLDER_LABEL
        res.extra["older_than_cs6"] = True
        return res

    res.src_format_byte = fmt_int
    res.src_major = major
    res.src_version = "%.1f" % major
    res.extra["sub_ver"] = sub_ver
    res.extra["head_offset"] = head.data_offset

    res.hits.append(VersionHit(fmt_off, 4, "format_byte", fmt_int,
                               note="head 块 · 格式字节"))
    return res


# --------------------------------------------------------------------------
# 扫描：AEPX（XML）
# --------------------------------------------------------------------------
#
# 真实样本（AE CC 2018 保存的空工程，经公开资料核实）结构如下：
#
#   <?xml version="1.0" encoding="UTF-8"?>
#   <AfterEffectsProject xmlns="..." majorVersion="1" minorVersion="0">
#     <svap bdata="07789645"/>                              ← 版本字（4 字节）
#     <head bdata="005c000e07789645800000000000000c00000019"/>  ← 20 字节，与 AEP head 块数据区一致
#     <nhed bdata="..."/>
#     ...
#
# 即：AEPX 把 RIFX 的每个 chunk 序列化成一个 XML 元素，二进制数据区放进 bdata 属性，
# 写成连续的小写十六进制串。所以 head 的 bdata 布局与 .aep 的 head 块完全一致：
#
#   hex[0:2]  = 00          hex[2:4]  = 格式字节      hex[4:6] = 00
#   hex[6:8]  = 子版本      hex[8:16] = 版本字(u32be)
#
# 自校验：把样本里的 07789645 按位域解码 = 15.1.2 build 69 Macintosh，
#         与 hex[2:4] 的 5c（0x5C = CC 2018 = 15.x）完全自洽。
#
# 注意：scan_aepx 收到的 text 是 bytes 按 latin-1 解码的结果（1 字节 = 1 字符），
#       这样命中的偏移可直接用于字节级补丁，中文等 UTF-8 多字节内容不会造成错位。

_BDATA_RE = re.compile(r"""bdata\s*=\s*(?:"([0-9A-Fa-f]*)"|'([0-9A-Fa-f]*)')""")
_HEAD_TAG_RE = re.compile(r"<head\b[^>]*>")
_SVAP_TAG_RE = re.compile(r"<svap\b[^>]*>")

# 兜底用：找不到 head 标签时，退化为全文扫描内嵌十六进制头
_HEX_HEAD_RE = re.compile(
    r"(00)([0-9A-Fa-f]{2})(00)([0-9A-Fa-f]{2})([0-9A-Fa-f]{8})"
)


def _tag_bdata_spans(text: str, tag_re) -> List[Tuple[int, int, str]]:
    """在匹配 tag_re 的标签内找 bdata 属性，返回 (hex 起始偏移, 结束偏移, hex 串)。"""
    out: List[Tuple[int, int, str]] = []
    for tm in tag_re.finditer(text):
        seg = text[tm.start():tm.end()]
        for bm in _BDATA_RE.finditer(seg):
            val = bm.group(1) if bm.group(1) is not None else bm.group(2)
            gi = 1 if bm.group(1) is not None else 2
            s = tm.start() + bm.start(gi)
            out.append((s, s + len(val), val))
    return out


def scan_aepx(text: str, path: str = "") -> ScanResult:
    res = ScanResult(path=path, ftype="aepx", size=len(text))

    chosen: Optional[Tuple[int, int, str, int, int]] = None   # (s, e, hex, fmt_byte, word)
    for s, e, hexstr in _tag_bdata_spans(text, _HEAD_TAG_RE):
        if len(hexstr) < 16:
            continue
        if hexstr[0:2].lower() != "00" or hexstr[4:6].lower() != "00":
            continue
        fmt_byte = int(hexstr[2:4], 16)
        word = int(hexstr[8:16], 16)
        d = decode_word(word)
        # 合理性过滤：平台代号已知，且版本字解码出的 major 与格式字节自洽
        if d["os"] not in OS_NAMES:
            continue
        kind, major = classify_format_byte(fmt_byte, d["major"])
        if kind == "newer" or kind == "older":
            continue
        if chosen is None or d["major"] > chosen[4]:
            chosen = (s, e, hexstr, fmt_byte, word)
        if kind == "inferred":
            res.extra["inferred"] = True
            res.extra["infer_note"] = infer_note(fmt_byte, major)

    if chosen is None:
        return _scan_aepx_fallback(text, res)

    s, e, hexstr, fmt_byte, word = chosen
    d = decode_word(word)
    res.src_format_byte = fmt_byte
    res.src_word = word
    res.src_major = d["major"]
    res.src_os = d["os"]
    res.src_build = d["build"]
    res.src_version = d["version"]
    res.extra["sub_ver"] = int(hexstr[6:8], 16)
    res.extra["platform"] = d["os_name"]

    # hit 1：head 内的格式字节（hex[2:4]）
    res.hits.append(VersionHit(s + 2, 2, "format_byte_hex", fmt_byte,
                               note="head 元素 · bdata 格式字节"))
    # hit 2：head 内的版本字（hex[8:16]）
    res.hits.append(VersionHit(s + 8, 8, "word_hex", word,
                               note="head 元素 · bdata 版本字"))

    # hit 3：svap 元素（内含同一份版本字，与 head 核对后再改）
    for ss, se, sh in _tag_bdata_spans(text, _SVAP_TAG_RE):
        if len(sh) < 8:
            continue
        sv = int(sh[0:8], 16)
        if sv == word:
            res.hits.append(VersionHit(ss, 8, "word_hex", sv,
                                       note="svap 元素 · bdata 版本字"))
            break
        res.extra.setdefault("svap_mismatch", sv)
    return res


def _scan_aepx_fallback(text: str, res: ScanResult) -> ScanResult:
    """
    兜底：没有标准 head 元素时，全文扫描内嵌十六进制头。
    只在能通过平台代号 + major 自洽双重校验时才动手，并明确标注风险。
    """
    hits: List[VersionHit] = []
    found_major = 0.0
    found_word = 0
    for m in _HEX_HEAD_RE.finditer(text):
        fmt_byte = int(m.group(2), 16)
        word = int(m.group(5), 16)
        kind, major = classify_format_byte(fmt_byte)
        if kind != "known":
            continue
        d = decode_word(word)
        if d["os"] not in OS_NAMES:
            continue
        if abs(d["major"] - major) > 2.0:
            continue
        hits.append(VersionHit(m.start(2), 2, "format_byte_hex", fmt_byte,
                               note="内嵌十六进制块 · 格式字节(兜底)"))
        hits.append(VersionHit(m.start(5), 8, "word_hex", word,
                               note="内嵌十六进制块 · 版本字(兜底)"))
        if d["major"] > found_major:
            found_major = d["major"]
            found_word = word
        res.src_format_byte = fmt_byte
    if not hits:
        res.ok = False
        res.error = ("未能在 XML 中定位到版本头（既没有标准 <head bdata>，"
                     "也没找到可校验的内嵌十六进制头）。本工具不会在无法确认时改写文件。")
        return res
    res.hits = hits
    res.src_major = found_major
    res.src_word = found_word
    res.src_version = decode_word(found_word)["version"] if found_word else "%.1f" % found_major
    res.extra["aepx_fallback"] = True
    return res


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------

def scan_bytes(data: bytes, path: str = "") -> ScanResult:
    ext = os.path.splitext(path)[1].lower()
    # AEPX 用 latin-1 解码：1 字节 = 1 字符，命中的偏移可直接用于字节级补丁，
    # 中文/日文等 UTF-8 多字节内容不会造成错位（用 utf-8 解码会错位，这是坑）。
    if ext == ".aepx" or (not data[:4] == b"RIFX" and data.lstrip()[:6] == b"<?xml "):
        try:
            return scan_aepx(data.decode("latin-1"), path)
        except Exception as e:
            r = ScanResult(path=path, ftype="aepx")
            r.ok = False
            r.error = "AEPX 解码失败：%s" % e
            return r
    if data[:4] == b"RIFX":
        if data[8:12] == b"FaFX":
            return scan_ffx(data, path)
        if data[8:12] == b"Egg!":
            return scan_aep(data, path)
    # 扩展名兜底
    if ext == ".ffx":
        return scan_ffx(data, path)
    if ext == ".aep":
        return scan_aep(data, path)
    r = ScanResult(path=path, ftype="unknown", size=len(data))
    r.ok = False
    r.error = "无法识别的文件类型（支持 .aep / .ffx / .aepx）"
    return r


def scan_file(path: str) -> ScanResult:
    with open(path, "rb") as f:
        data = f.read()
    return scan_bytes(data, path)


# --------------------------------------------------------------------------
# 目标版本计算
# --------------------------------------------------------------------------

def compute_new_values(res: ScanResult, target_major: float,
                       aggressive: bool = False) -> None:
    """就地计算每个 hit 的新值。"""
    tgt_byte = format_byte_of(target_major)
    if tgt_byte is None:
        raise RifxError("目标版本 %.1f 不在支持列表中" % target_major)
    tgt_major_int = int(round(target_major))
    for h in res.hits:
        if h.kind == "format_byte":
            h.new_value = tgt_byte
        elif h.kind == "word":
            h.new_value = reset_word(h.old_value, tgt_major_int) if aggressive \
                else replace_major_in_word(h.old_value, tgt_major_int)
        elif h.kind == "format_byte_hex":
            h.new_value = tgt_byte
        elif h.kind == "word_hex":
            h.new_value = reset_word(h.old_value, tgt_major_int) if aggressive \
                else replace_major_in_word(h.old_value, tgt_major_int)


def apply_patch(data: bytes, res: ScanResult) -> bytes:
    """把计算好的新值写进数据副本。"""
    buf = bytearray(data)
    for h in res.hits:
        if h.kind.endswith("_hex"):
            # aepx：文本态，按十六进制字符串替换，长度必须严格不变
            width = h.length
            orig = data[h.offset:h.offset + width]
            # 沿用原文的大小写风格（实测 AEPX 用小写，但不要假设）
            upper = any(0x41 <= b <= 0x46 for b in orig)
            text = ("%0*X" if upper else "%0*x") % (width, h.new_value & (16 ** width - 1))
            buf[h.offset:h.offset + width] = text.encode("latin-1")
        elif h.length == 1:
            if h.new_value > 0xFF:
                raise RifxError("单字节补丁的值越界")
            buf[h.offset] = h.new_value
        elif h.length == 4:
            buf[h.offset:h.offset + 4] = struct.pack(">I", h.new_value & 0xFFFFFFFF)
        else:
            raise RifxError("不支持的补丁长度 %d" % h.length)
    return bytes(buf)


# --------------------------------------------------------------------------
# 转换单个文件
# --------------------------------------------------------------------------

@dataclass
class ConvertResult:
    path: str
    out_path: str = ""
    ok: bool = False
    status: str = ""        # converted | already_lower | skipped | failed
    message: str = ""
    src_version: str = ""
    dst_version: str = ""
    hits: int = 0
    verify: str = ""


def convert_file(src: str, dst: str, target_major: float,
                 aggressive: bool = False, dry_run: bool = False,
                 overwrite: bool = False) -> ConvertResult:
    """
    把 src 转换成目标版本，写入 dst。原文件永远不动。
    """
    r = ConvertResult(path=src, dst_version=label_of_major(target_major))
    try:
        with open(src, "rb") as f:
            data = f.read()
    except Exception as e:
        r.status = "failed"
        r.message = "读取失败：%s" % e
        return r

    res = scan_bytes(data, src)
    if not res.ok:
        r.status = "failed"
        r.message = res.error
        return r

    if res.extra.get("older_than_cs6"):
        r.src_version = OLDER_LABEL
    else:
        r.src_version = "%s (%s)" % (res.src_version, label_of_major(res.src_major))

    notes: List[str] = []
    if res.extra.get("inferred"):
        notes.append("⚠ " + res.extra["infer_note"])
    if res.extra.get("aepx_fallback"):
        notes.append("⚠ 未找到标准 <head> 元素，已按模式匹配兜底改写，请务必用目标版本 AE 实机确认")
    if notes:
        r.message = " ".join(notes)

    # 已经是目标版本或更低 -> 原样复制，保证输出目录结构完整
    if res.src_major <= target_major + 1e-6:
        r.status = "already_lower"
        r.message = ("源版本 %s 不高于目标 %s，原样复制"
                     % (r.src_version or "?", r.dst_version))
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
        compute_new_values(res, target_major, aggressive)
    except RifxError as e:
        r.status = "failed"
        r.message = str(e)
        return r

    new_data = apply_patch(data, res)
    r.hits = len(res.hits)

    if len(new_data) != len(data):
        r.status = "failed"
        r.message = "补丁后文件长度发生变化，已中止（不应发生）"
        return r

    # 往返验证：重新解析，确认读出的 major 就是目标版本
    chk = scan_bytes(new_data, src)
    if chk.ok and abs(chk.src_major - target_major) > 1e-6:
        r.status = "failed"
        r.message = ("往返验证失败：期望 %s，实际读出 %s，已中止"
                     % (r.dst_version, label_of_major(chk.src_major)))
        return r
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
        r.message = "已改写 %d 处版本标记" % r.hits
    return r


def _ensure_dir(d: str) -> None:
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------
# 批量 / 递归
# --------------------------------------------------------------------------

SUPPORTED_EXT = (".aep", ".ffx", ".aepx")


def iter_files(root: str, recursive: bool = True) -> List[str]:
    if os.path.isfile(root):
        return [root]
    out = []
    if recursive:
        for dp, dn, fn in os.walk(root):
            dn[:] = [x for x in dn if not x.startswith(".")]
            for n in fn:
                if n.lower().endswith(SUPPORTED_EXT):
                    out.append(os.path.join(dp, n))
    else:
        for n in sorted(os.listdir(root)):
            p = os.path.join(root, n)
            if os.path.isfile(p) and n.lower().endswith(SUPPORTED_EXT):
                out.append(p)
    return out


def convert_paths(paths: List[str], target_major: float, out_dir: str = "",
                  in_place: bool = False, recursive: bool = True,
                  aggressive: bool = False, dry_run: bool = False,
                  flat: bool = False) -> List[ConvertResult]:
    """
    paths 可以是文件也可以是目录。
    out_dir 为空且非 in_place 时，默认在每个输入目录下生成 <目标版本> 子目录。
    """
    files: List[str] = []
    for p in paths:
        files.extend(iter_files(p, recursive))

    files = sorted(set(os.path.abspath(f) for f in files))
    results: List[ConvertResult] = []

    # 计算输出根目录（用于保持相对目录结构）
    base = ""
    if files:
        try:
            cp = os.path.commonpath(files)
            base = cp if os.path.isdir(cp) else os.path.dirname(cp)
        except ValueError:
            base = ""   # 跨盘符，退化为只取文件名

    tag = "AE%s" % (("%.1f" % target_major).replace(".", "_"))

    for src in files:
        if in_place:
            dst = src
        elif out_dir:
            if flat:
                dst = os.path.join(out_dir, os.path.basename(src))
            else:
                rel = os.path.relpath(src, base) if base else os.path.basename(src)
                dst = os.path.join(out_dir, tag, rel)
        else:
            dst = os.path.join(os.path.dirname(src), tag, os.path.basename(src))
        results.append(convert_file(src, dst, target_major,
                                    aggressive=aggressive, dry_run=dry_run))
    return results


# --------------------------------------------------------------------------
# 风险等级
# --------------------------------------------------------------------------

def risk_level(src_major: float, dst_major: float) -> Tuple[str, str]:
    """返回 (等级, 说明)。"""
    span = src_major - dst_major
    if span <= 0:
        return ("无需处理", "源版本不高于目标")
    if span <= OFFICIAL_BACK_STEPS:
        return ("推荐", "在 Adobe 官方「另存为低版本」支持的跨度内，成功率最高")
    if span <= 5:
        return ("实验性", "超出官方跨度，二进制结构差异可能导致部分内容丢失")
    return ("高风险", "跨度过大，强烈建议先用副本试开")
