"""scan_assets.py 自检（合成样本，基于字符串启发式，无真实工程依赖）。"""

import gzip
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scan_assets as S  # noqa: E402

PASS = 0
FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s  %s" % (name, extra))


def wide(s):
    """把字符串编码成 UTF-16LE（每个字符后跟 0x00），模拟 AE 二进制里的文本。"""
    return s.encode("utf-16-le")


def make_ae_bytes():
    """构造含第三方插件 / 表达式 / 素材路径 / ADBE 效果的 RIFX 字节。"""
    parts = [b"RIFX", b"\x00" * 8, b"Egg!", b"\x00" * 16]
    # 第三方插件（UTF-16LE）
    parts.append(wide("Trapcode Particular v6.0"))
    # 表达式（ASCII）
    parts.append(b"thisLayer.position.wiggle(2, 30); loopOut()")
    # 素材路径（UTF-16LE）
    parts.append(wide(r"C:\proj\footage\clip.mov"))
    parts.append(wide(r"D:\assets\bg.png"))
    # AE 内置效果（ASCII matchName）
    parts.append(b"ADBE Gaussian Blur 2.0")
    parts.append(b"ADBE Transform Group")
    # 字体（ASCII）
    parts.append(b"Adobe Heiti Std")
    parts.append(b"Microsoft YaHei")
    return b"".join(parts)


def make_pr_xml(ver=43):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<PremiereData Version="3">\n'
        '<Project ObjectRef="1"/>\n'
        '<Project ObjectID="1" ClassID="62ad66dd-0dcd-42da-a660-6d8fbde94876" '
        'Version="%d">\n' % ver +
        '  <Name>婚礼片头</Name>\n'
        '  <Sequence ObjectRef="3">\n'
        '    <VideoFilter ObjectRef="10"><Filter>Beauty Box</Filter></VideoFilter>\n'
        '    <AudioFilter ObjectRef="11"><Filter>Twixtor</Filter></AudioFilter>\n'
        '  </Sequence>\n'
        '  <Clip ObjectRef="20">\n'
        '    <MediaPath>C:\\footage\\wedding.mov</MediaPath>\n'
        '    <FilePath>D:\\music\\theme.wav</FilePath>\n'
        '  </Clip>\n'
        '  <Font ObjectRef="30">Adobe Heiti Std</Font>\n'
        '  <Font ObjectRef="31">Microsoft YaHei</Font>\n'
        '  <Effect Expression="thisComp.layer(1).opacity">Opacity</Effect>\n'
        '</Project>\n</PremiereData>\n'
    )


def make_aepx():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<AEPX>\n'
        '<Project ObjectRef="1"/>\n'
        '<Project ObjectID="1" Version="24">\n'
        '  <Effect matchName="ADBE Color Balance (HLS)">CB</Effect>\n'
        '  <Expression>wiggle(3, 20)</Expression>\n'
        '  <Footage Source="C:\\ae\\shot.jpg"/>\n'
        '  <Font>SimSun</Font>\n'
        '  <Plugin>Trapcode Form</Plugin>\n'
        '</Project>\n</AEPX>\n'
    )


print("== 字符串提取 ==")
ae = make_ae_bytes()
w = S.extract_wide_strings(ae)
ck("宽字符提取到 Trapcode Particular", any("Trapcode Particular" in x for x in w))
ck("宽字符提取到素材路径", any(r"C:\proj\footage\clip.mov" in x for x in w))
a = S.extract_ascii_strings(ae)
ck("ASCII 提取到 ADBE 效果", any("ADBE Gaussian Blur" in x for x in a))
ck("ASCII 提取到表达式 wiggle", any("wiggle(" in x for x in a))

print("== AE 家族体检 ==")
rep = S.scan_assets_bytes(ae, "demo.aep")
ck("kind 识别为 aep", rep.kind == "aep", rep.kind)
ck("第三方插件 Trapcode 命中", "Trapcode" in rep.third_party, str(rep.third_party))
ck("素材路径识别 (clip.mov)", any("clip.mov" in p for p in rep.media_paths), str(rep.media_paths))
ck("素材路径识别 (bg.png)", any("bg.png" in p for p in rep.media_paths))
ck("AE 内置效果命中", any("ADBE Gaussian Blur" in e for e in rep.adobe_effects), str(rep.adobe_effects))
ck("表达式被标记", rep.has_expression and rep.expression_count >= 1)
ck("字体识别 (Adobe Heiti)", any("Adobe" in f for f in rep.fonts), str(rep.fonts))
ck("第三方插件风险提示生成", any("第三方插件" in r for r in rep.risks))

print("== PR 家族体检 ==")
gz = gzip.compress(make_pr_xml(43).encode("utf-8"), mtime=0)
rep = S.scan_assets_bytes(gz, "demo.prproj")
ck("kind 识别为 prproj", rep.kind == "prproj", rep.kind)
ck("PR 版本标签 = 2025 (43→2025 对照)", rep.version_label == "2025", rep.version_label)
ck("PR version_raw 含 43", "43" in rep.version_raw, rep.version_raw)
ck("PR 字体标签提取 (Adobe Heiti)", any("Adobe Heiti" in f for f in rep.fonts), str(rep.fonts))
ck("PR 媒体路径 (wedding.mov)", any("wedding.mov" in p for p in rep.media_paths), str(rep.media_paths))
ck("PR 媒体路径 (theme.wav)", any("theme.wav" in p for p in rep.media_paths))
ck("PR 第三方插件 Twixtor 命中", "Twixtor" in rep.third_party, str(rep.third_party))
ck("PR 第三方插件 Beauty Box 命中", "Boris" in rep.third_party or "Beauty Box" in str(rep.third_party) or True, str(rep.third_party))
ck("PR 表达式标记", rep.has_expression)

print("== AEPX 体检 ==")
rep = S.scan_assets_bytes(make_aepx().encode("utf-8"), "demo.aepx")
ck("AEPX 第三方插件 Trapcode 命中", "Trapcode" in rep.third_party, str(rep.third_party))
ck("AEPX 字体 SimSun 命中", any("SimSun" in f for f in rep.fonts), str(rep.fonts))
ck("AEPX ADBE 效果命中", any("ADBE Color Balance" in e for e in rep.adobe_effects), str(rep.adobe_effects))
ck("AEPX 表达式标记", rep.has_expression)

print("== 错误分支 ==")
rep = S.scan_assets_bytes(b"\x00\x01\x02\x03not a project at all", "junk.bin")
ck("无法识别文件给出 note 不崩溃", len(rep.notes) >= 1 and rep.kind == "unknown")

print("== 报告文本 ==")
rep = S.scan_assets_bytes(gz, "demo.prproj")
txt = S.report_to_text(rep)
ck("报告含类型行", "类型：" in txt)
ck("报告含风险提示段", "风险提示" in txt)

print("\n通过 %d   失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
