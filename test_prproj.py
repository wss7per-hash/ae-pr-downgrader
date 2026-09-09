# -*- coding: utf-8 -*-
"""
test_prproj.py —— Premiere Pro 工程（.prproj）降级核心自检

本机没有安装 Premiere Pro，因此用**合成的 .prproj 样本**验证：
gzip 解包/回包、版本标记定位、改写、往返校验、原文件其余字节不变。
覆盖 gzip / 纯 XML / Version=1 兜底 / 各类错误分支。
"""

from __future__ import annotations

import gzip
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prproj_core as P

PASS = 0
FAIL = 0
FAILED = []


def ck(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  [FAIL] %s   %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


# ---------------------------------------------------------------- 样本构造

def make_xml(ver, cn="测试片段 · 中文素材"):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<PremiereData Version="3">\n'
        '<Project ObjectRef="1"/>\n'
        '<Project ObjectID="1" ClassID="62ad66dd-0dcd-42da-a660-6d8fbde94876" '
        'Version="%d">\n' % ver +
        '  <Name>%s</Name>\n' % cn +
        '  <ProjectViewState Version="7" ObjectRef="2"/>\n'
        '  <Sequence ObjectRef="3"/>\n'
        '</Project>\n'
        '</PremiereData>\n'
    )


def make_gzip(ver, **kw):
    return gzip.compress(make_xml(ver, **kw).encode("utf-8"), mtime=0)


print("=" * 72)
print("test_prproj.py —— Premiere Pro 工程降级自检")
print("=" * 72)

# ---------------------------------------------------------------- 1. 对照表
print("\n[1] 版本对照表")
eq("2025 → 43", P.BY_LABEL.get("2025"), 43)
eq("2024 → 42", P.BY_LABEL.get("2024"), 42)
eq("2023 → 41", P.BY_LABEL.get("2023"), 41)
eq("2022 → 40", P.BY_LABEL.get("2022"), 40)
eq("2021 → 39", P.BY_LABEL.get("2021"), 39)
eq("CC 2020 → 38", P.BY_LABEL.get("cc 2020"), 38)
eq("43 → 2025", P.BY_NUM.get(43), "2025")
eq("表内最大值", P.MAX_NUM, 45)
eq("2026 标记为 45", P.BY_LABEL.get("2026"), 45)
eq("通用兜底号", P.PR_SAFE_ANY, 1)

# ---------------------------------------------------------------- 2. 扫描
print("\n[2] 扫描：gzip 工程")
data = make_gzip(43)
r = P.scan_prproj_bytes(data, "demo.prproj")
ck("扫描成功", r.ok, r.error)
eq("识别为 gzip", r.was_gzip, True)
eq("源版本号", r.src_number, 43)
eq("源版本标签", r.src_label, "2025")
eq("命中 1 处", len(r.hits), 1)
eq("命中位置的值", r.hits[0].old_value, 43)
ck("hit 偏移处确实是版本号",
   r.xml[r.hits[0].offset:r.hits[0].offset + r.hits[0].length] == "43",
   r.xml[r.hits[0].offset - 30:r.hits[0].offset + 10])
ck("没有误命中 ProjectViewState 的 Version=7",
   r.hits[0].note.find("根 Project") >= 0)

print("\n[3] 扫描：未压缩的纯 XML（老工程）")
plain = make_xml(36).encode("utf-8")
r2 = P.scan_prproj_bytes(plain, "old.prproj")
ck("扫描成功", r2.ok, r2.error)
eq("识别为非 gzip", r2.was_gzip, False)
eq("源版本号", r2.src_number, 36)
eq("源版本标签", r2.src_label, "CC 2019")

print("\n[4] 扫描：2026 工程带稀疏序列化标记")
r26 = P.scan_prproj_bytes(make_gzip(45), "y2026.prproj")
ck("扫描成功", r26.ok, r26.error)
eq("源版本号", r26.src_number, 45)
ck("标记 sparse_2026", r26.extra.get("sparse_2026") is True)

# ---------------------------------------------------------------- 5. 转换
print("\n[5] 转换：43 → 42（gzip）")
out, res = P.convert_prproj_bytes(make_gzip(43), 42, "demo.prproj")
txt = gzip.decompress(out).decode("utf-8")
ck("输出仍是 gzip", out[:2] == b"\x1f\x8b")
ck("XML 里已是 42", 'Version="42"' in txt)
ck("不再有 43", 'Version="43"' not in txt)
ck("中文内容保留", "测试片段 · 中文素材" in txt)
ck("ProjectViewState 未被误改", 'ProjectViewState Version="7"' in txt)
ck("ClassID 未变", "62ad66dd-0dcd-42da-a660-6d8fbde94876" in txt)
eq("重新扫描读到 42", P.scan_prproj_bytes(out).src_number, 42)
# 除版本号外，其余字符完全一致
ck("除版本号外内容完全一致",
   txt.replace('Version="42"', 'Version="43"') == make_xml(43))

print("\n[6] 转换：43 → 1（通用兜底，位数变化）")
out1, _ = P.convert_prproj_bytes(make_gzip(43), P.PR_SAFE_ANY, "demo.prproj")
t1 = gzip.decompress(out1).decode("utf-8")
ck("XML 里已是 1", 'Version="1"' in t1)
eq("重新扫描读到 1", P.scan_prproj_bytes(out1).src_number, 1)
ck("回改 43 后与原文本一致",
   t1.replace('Version="1"', 'Version="43"') == make_xml(43))

print("\n[7] 转换：纯 XML 保持不压缩输出")
outp, _ = P.convert_prproj_bytes(plain, 34, "old.prproj")
ck("输出不是 gzip", outp[:2] != b"\x1f\x8b")
ck("内容是 XML", outp.decode("utf-8").find('Version="34"') > 0)

print("\n[8] 转换：源版本已是目标 → 抛错而非乱改")
try:
    P.convert_prproj_bytes(make_gzip(42), 42)
    ck("同版本应抛错", False)
except P.PrprojError as e:
    ck("同版本抛 PrprojError", True)
    ck("错误信息友好", "无需改写" in str(e), str(e))

# ---------------------------------------------------------------- 9. 错误分支
print("\n[9] 错误分支")
r9 = P.scan_prproj_bytes(b"hello world, not a project", "x.prproj")
ck("非 gzip 非 XML → 失败", not r9.ok)
ck("给出可读错误", len(r9.error) > 0)

gz_not_pr = gzip.compress(b"<html><body>nope</body></html>")
r10 = P.scan_prproj_bytes(gz_not_pr, "x.prproj")
ck("gzip 但不是 PR XML → 失败", not r10.ok)
ck("提示内容不像 Premiere 工程", "Premiere" in r10.error, r10.error)

no_ver = gzip.compress(b'<?xml version="1.0"?>\n<PremiereData Version="3">\n</PremiereData>')
r11 = P.scan_prproj_bytes(no_ver, "x.prproj")
ck("缺 Version 属性 → 失败", not r11.ok)

r12 = P.scan_prproj_bytes(b"\x1f\x8b\x08\x00garbagegarbage", "x.prproj")
ck("损坏 gzip → 失败（不抛异常）", not r12.ok)
ck("损坏 gzip 给出可读错误", "解压失败" in r12.error, r12.error)

# ---------------------------------------------------------------- 10. 目标解析
print("\n[10] 目标版本解析")
eq("'2024'", P.resolve_pr_target("2024"), 42)
eq("'PR2023'", P.resolve_pr_target("PR2023"), 41)
eq("'any'", P.resolve_pr_target("any"), 1)
eq("'1'", P.resolve_pr_target("1"), 1)
eq("数字 42", P.resolve_pr_target(42), 42)
eq("'CC 2018'", P.resolve_pr_target("cc2018"), 34)
for bad in ("1999", "abc", "99"):
    try:
        P.resolve_pr_target(bad)
        ck("非法目标 %r 应抛错" % bad, False)
    except ValueError:
        ck("非法目标 %r 抛 ValueError" % bad, True)

# ---------------------------------------------------------------- 11. 风险等级
print("\n[11] 风险等级")
eq("43→42 推荐", P.risk_level(43, 42)[0], "推荐")
eq("43→40 实验性", P.risk_level(43, 40)[0], "实验性")
eq("43→36 高风险", P.risk_level(43, 36)[0], "高风险")
eq("42→43 无需处理", P.risk_level(42, 43)[0], "无需处理")

# ---------------------------------------------------------------- 12. 文件级
print("\n[12] 文件级转换（含目录结构、dry-run、原文件不动）")
tmp = tempfile.mkdtemp(prefix="prproj_test_")
src = os.path.join(tmp, "demo.prproj")
with open(src, "wb") as f:
    f.write(make_gzip(43))
before = open(src, "rb").read()

dst = os.path.join(tmp, "PR2024", "demo.prproj")
res_f = P.convert_prproj_file(src, dst, 42)
eq("状态 converted", res_f.status, "converted")
ck("输出文件存在", os.path.exists(dst))
eq("重新扫描输出", P.scan_prproj_bytes(open(dst, "rb").read()).src_number, 42)
eq("原文件未被改动", open(src, "rb").read(), before)
ck("往返校验标记", res_f.verify == "OK")
ck("源版本描述含 2025", "2025" in res_f.src_version, res_f.src_version)

res_dry = P.convert_prproj_file(src, os.path.join(tmp, "nope", "x.prproj"), 42, dry_run=True)
eq("dry-run 也返回 converted", res_dry.status, "converted")
ck("dry-run 不落盘", not os.path.exists(os.path.join(tmp, "nope")))

res_low = P.convert_prproj_file(src, os.path.join(tmp, "PR2025", "demo.prproj"), 43)
eq("目标=源 → already_lower", res_low.status, "already_lower")

res_2026 = P.convert_prproj_file(
    src.replace("demo", "y26"), os.path.join(tmp, "out", "y26.prproj"), 42)
eq("文件不存在 → failed", res_2026.status, "failed")

# 2026 警告
src26 = os.path.join(tmp, "y26.prproj")
with open(src26, "wb") as f:
    f.write(make_gzip(45))
res26 = P.convert_prproj_file(src26, os.path.join(tmp, "o26", "y26.prproj"), 42)
eq("2026 转换成功", res26.status, "converted")
ck("带 2026 稀疏序列化警告", "2026" in (res26.message or ""), res26.message)

# ---------------------------------------------------------------- 13. 幂等
print("\n[13] 幂等性：同样输入两次结果一致")
a1, _ = P.convert_prproj_bytes(make_gzip(43), 42)
a2, _ = P.convert_prproj_bytes(make_gzip(43), 42)
eq("字节完全一致（mtime=0）", a1, a2)

# ---------------------------------------------------------------- 结果
print("\n" + "=" * 72)
print("通过 %d   失败 %d" % (PASS, FAIL))
if FAILED:
    print("失败项：")
    for n in FAILED:
        print("   - " + n)
print("=" * 72)
sys.exit(1 if FAIL else 0)
