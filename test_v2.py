# -*- coding: utf-8 -*-
"""
test_v2.py —— 验证新增的两项能力：26.x 支持 与 .aepx 支持

样本来源：
  secret.aep                        AE 2025 自带的真实工程（25.0.0 build 47 Windows）
  Cracked Tiles.ffx                 AE 2025 预设库真实文件
  aepx 样本                         用 secret.aep 的 head 块真实字节 + 公开资料核实的
                                    AEPX 文档骨架拼出（bdata 布局与真实样本一致）
"""

import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aep_core as C

PASS = 0
FAIL = 0


def chk(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s   %s" % (name, extra))


AE = r"C:/Program Files/Adobe/Adobe After Effects 2025/Support Files"
AEP = AE + "/Required/secret.aep"
FFX = AE + "/Presets/Image - Special Effects/Cracked Tiles.ffx"

print("== 0. 位域自校验（公开样本：CC 2018 15.1.2 Macintosh）==")
# 真实 AEPX 样本里的 <head bdata="005c000e07789645...">
d = C.decode_word(0x07789645)
chk("major=15", d["major"] == 15, str(d))
chk("minor=1", d["minor"] == 1, str(d))
chk("patch=2", d["patch"] == 2, str(d))
chk("build=69", d["build"] == 69, str(d))
chk("os=13 Mac", d["os"] == 13, str(d))
chk("格式字节 0x5C 自洽", C.classify_format_byte(0x5C, 15)[1] == 15.0)

print("\n== 1. 26.x 已入表 ==")
chk("26.0 格式字节 = 0x61", C.format_byte_of(26.0) == 0x61,
    hex(C.format_byte_of(26.0) or 0))
chk("0x61 → 26.0", C.classify_format_byte(0x61)[0] == "known")
chk("线性规律 22/23/24/25/26 均成立",
    all(C.format_byte_of(m) == m + C.LINEAR_BASE for m in (22, 23, 24, 25, 26)))

print("\n== 2. 未知高版本的交叉校验推断（模拟 27.x）==")
kind, mj = C.classify_format_byte(0x62, 27)     # 外推值 27 与版本字一致 → 采信
chk("0x62 + word major 27 → inferred", kind == "inferred" and mj == 27.0, "%s %s" % (kind, mj))
kind2, _ = C.classify_format_byte(0x62, 25)     # 外推值 27 与版本字 25 不符 → 拒绝
chk("两者对不上 → 拒绝", kind2 == "newer", kind2)
kind3, _ = C.classify_format_byte(0x59, 18)     # 落在空隙里 → 拒绝
chk("落在空隙 → 拒绝", kind3 == "newer", kind3)

print("\n== 3. 26.x 工程端到端降级（用真实 secret.aep 改造）==")
raw = open(AEP, "rb").read()
u26 = bytearray(raw)
# 把 head 的格式字节改成 0x61，并把版本字的 major 改成 26
h = C.scan_bytes(raw, "secret.aep")
fmt_off = next(x.offset for x in h.hits if x.kind == "format_byte")
word_off = next(x.offset for x in h.hits if x.kind == "word")
u26[fmt_off] = 0x61
w = struct.unpack(">I", raw[word_off:word_off + 4])[0]
nw = C.replace_major_in_word(w, 26)
u26[word_off:word_off + 4] = struct.pack(">I", nw)
# 同步 svap
for x in h.hits:
    if x.kind == "word" and x.offset != word_off:
        u26[x.offset:x.offset + 4] = struct.pack(">I", nw)
u26 = bytes(u26)

r26 = C.scan_bytes(u26, "fake26.aep")
chk("识别为 26.x", r26.ok and r26.src_major == 26.0,
    "%s %s" % (r26.ok, r26.error or r26.src_major))
C.compute_new_values(r26, 24.0)
out26 = C.apply_patch(u26, r26)
chk("长度不变", len(out26) == len(u26))
# 26->24：major 高 6 位相同（26>>3 == 24>>3 == 3），只改低 3 位，
# 因此每处版本字只动 1 字节（偏移 21 是 svap，37 是 head），外加格式字节 33。
chk("最小改动：只改 3 个字节",
    len([i for i in range(len(u26)) if u26[i] != out26[i]]) == 3,
    str([i for i in range(len(u26)) if u26[i] != out26[i]]))
v = C.scan_bytes(out26, "fake26.aep")
chk("往返校验 → 24.x", v.ok and v.src_major == 24.0, str(v.src_major))

print("\n== 4. AEPX 支持（bdata 布局取自公开真实样本）==")
# 用 secret.aep 的 head 块真实字节（20 字节）生成 bdata，套进真实 AEPX 文档骨架
head_off = h.extra["head_offset"]
head_hex = raw[head_off:head_off + 20].hex()          # 小写，20 字节
svap_hex = head_hex[8:16]                              # 版本字，即 svap 的内容
aepx = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<AfterEffectsProject xmlns="http://www.adobe.com/products/aftereffects" '
    'majorVersion="1" minorVersion="0">\n'
    '\t<svap bdata="%s"/> <!--可能是版本号-->\n'
    '\t<head bdata="%s"/>\n'
    '\t<nhed bdata="0000000000000005000101001e10020000000019004cfac00000608000899000"/>\n'
    '\t<sfnm>\n\t\t<string>红色纯色</string>\n\t</sfnm>\n'
    '</AfterEffectsProject>\n' % (svap_hex, head_hex)
).encode("utf-8")

ra = C.scan_bytes(aepx, "proj.aepx")
chk("识别为 aepx", ra.ftype == "aepx")
chk("解析出 25.x", ra.ok and ra.src_major == 25.0, "%s %s" % (ra.ok, ra.error or ra.src_major))
chk("读到平台 Windows", ra.src_os == C.OS_WIN, str(ra.src_os))
chk("读到 build 47", ra.src_build == 47, str(ra.src_build))
chk("命中 3 处（head 格式字节 + head 版本字 + svap 版本字）",
    len(ra.hits) == 3, str([(x.kind, x.note) for x in ra.hits]))

C.compute_new_values(ra, 24.0)
out = C.apply_patch(aepx, ra)
chk("字节长度不变", len(out) == len(aepx), "%d vs %d" % (len(out), len(aepx)))
chk("仍是合法 UTF-8", out.decode("utf-8") is not None)
chk("仍是小写 hex", b"005F" not in out and b"005f" in out)

va = C.scan_bytes(out, "proj.aepx")
chk("往返校验 → 24.x", va.ok and va.src_major == 24.0, str(va.src_major))
chk("中文内容未被破坏", "红色纯色".encode("utf-8") in out)

diff = [i for i in range(len(aepx)) if aepx[i] != out[i]]
print("        改动偏移：%s" % diff)
for i in diff:
    print("          @%d  %s -> %s" % (i, bytes([aepx[i]]).decode(), bytes([out[i]]).decode()))
print("        改动后的 head：%s" %
      out.decode("utf-8").split("<head bdata=")[1].split("/>")[0])

print("\n== 5. AEPX 中文偏移正确性（UTF-8 多字节不能错位）==")
# 在 head 之前塞入大量中文，确认偏移计算依然是字节级
pad = "中文填充内容测试" * 50
aepx2 = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<AfterEffectsProject majorVersion="1" minorVersion="0">\n'
    '\t<!-- %s -->\n'
    '\t<svap bdata="%s"/>\n'
    '\t<head bdata="%s"/>\n'
    '</AfterEffectsProject>\n' % (pad, svap_hex, head_hex)
).encode("utf-8")
rb = C.scan_bytes(aepx2, "cn.aepx")
chk("中文前置仍解析出 25.x", rb.ok and rb.src_major == 25.0, "%s %s" % (rb.ok, rb.error))
C.compute_new_values(rb, 23.0)
outb = C.apply_patch(aepx2, rb)
vb = C.scan_bytes(outb, "cn.aepx")
chk("往返校验 → 23.x", vb.ok and vb.src_major == 23.0, str(vb.src_major))
chk("中文未被破坏", pad.encode("utf-8") in outb)
chk("长度不变", len(outb) == len(aepx2))

print("\n== 6. AEPX 兜底：无 head 元素时拒绝改写 ==")
bad = b'<?xml version="1.0" encoding="UTF-8"?>\n<AfterEffectsProject><Fold/></AfterEffectsProject>'
rc = C.scan_bytes(bad, "bad.aepx")
chk("无法确认时报失败而非乱改", rc.ok is False, rc.error)

print("\n== 7. 回归：真实 .aep / .ffx 未被改坏 ==")
r_aep = C.scan_bytes(raw, "secret.aep")
chk("secret.aep 仍识别为 25.0", r_aep.ok and r_aep.src_major == 25.0, str(r_aep.src_major))
ffx_raw = open(FFX, "rb").read()
r_ffx = C.scan_bytes(ffx_raw, "Cracked Tiles.ffx")
chk("ffx 仍能解析", r_ffx.ok, r_ffx.error)
if r_ffx.ok:
    C.compute_new_values(r_ffx, 23.0)
    of = C.apply_patch(ffx_raw, r_ffx)
    chk("ffx 长度不变", len(of) == len(ffx_raw))
    chk("ffx 往返 → 23.x", C.scan_bytes(of, "t.ffx").src_major == 23.0)

print("\n== 8. 版本表完整性 ==")
for m in (11, 12, 13, 14, 15, 16, 17, 18, 22, 23, 24, 25, 26):
    b = C.format_byte_of(float(m))
    chk("major %-2d → 格式字节 0x%02X" % (m, b or 0), b is not None)

print("\n" + "=" * 46)
print("  %d PASS / %d FAIL" % (PASS, FAIL))
print("=" * 46)
sys.exit(1 if FAIL else 0)
