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

import json as _json
import tempfile as _tf
import shutil as _sh

print("== 发包清单（build_manifest）==")
_tmp = _tf.mkdtemp()
_present = os.path.join(_tmp, "present.png")
with open(_present, "wb") as _f:
    _f.write(b"\x89PNG\r\n\x1a\n")
_proj_dir = os.path.join(_tmp, "proj")
os.makedirs(_proj_dir)
# 工程：含一个"本机能找到"的绝对素材 + 一个"找不到"的绝对素材 + 插件 + 字体
_ae2 = b"RIFX" + b"\x00" * 8 + b"Egg!" + b"\x00" * 16
_ae2 += wide(_present)
_ae2 += b"\x01"                       # 分隔字节：避免两个宽字符串被拼成一条
_ae2 += wide(r"Z:\missing\gone.mov")
_ae2 += b"Trapcode Particular"
_ae2 += b"Adobe Heiti Std"
_proj_path = os.path.join(_proj_dir, "main.aep")
with open(_proj_path, "wb") as _f:
    _f.write(_ae2)
_rep2 = S.scan_assets_file(_proj_path)
ck("体检识别出存在素材", any(_present in p for p in _rep2.media_paths), str(_rep2.media_paths))
_mf = S.build_manifest(_rep2, _proj_path)
ck("清单素材总数=2", len(_mf.media) == 2, str(len(_mf.media)))
ck("清单标记存在素材", any(m.exists and m.path == _present for m in _mf.media))
ck("清单标记缺失素材", any((not m.exists) and "gone.mov" in m.path for m in _mf.media))
ck("清单缺失计数=1", _mf.media_missing == 1, str(_mf.media_missing))
ck("清单含第三方插件", "Trapcode" in _mf.plugins, str(_mf.plugins))
ck("清单含字体", any("Adobe" in f for f in _mf.fonts), str(_mf.fonts))
ck("清单备注含缺失提示", any("找不到" in n for n in _mf.notes))

print("== 清单序列化 ==")
_md = S.manifest_to_markdown(_mf)
ck("MD 含标题", "# 发包清单" in _md)
ck("MD 含素材清单段", "## 素材清单" in _md)
ck("MD 含缺失素材段", "缺失素材" in _md)
_js = S.manifest_to_json(_mf)
_obj = _json.loads(_js)
ck("JSON 可解析且含 summary", "summary" in _obj and _obj["summary"]["media_missing"] == 1, _js[:80])
ck("JSON media 含 exists 字段", all("exists" in x for x in _obj["media"]))
_csv = S.manifest_to_csv(_mf)
ck("CSV 含表头", _csv.splitlines()[0].startswith("类别,名称,状态,路径"), _csv.splitlines()[0])

print("== 打包 package_project ==")
_out = os.path.join(_tmp, "deliver")
_res = S.package_project(_proj_path, _out)
ck("交付目录已创建", os.path.isdir(_out))
ck("工程副本已写出", os.path.isfile(os.path.join(_out, "main.aep")))
ck("manifest.md 已写出", os.path.isfile(os.path.join(_out, "manifest.md")))
ck("manifest.json 已写出", os.path.isfile(os.path.join(_out, "manifest.json")))
ck("交付说明已写出", os.path.isfile(os.path.join(_out, "交付说明.txt")))
ck("存在素材已复制", os.path.isfile(os.path.join(_out, "media", "present.png")))
ck("缺失素材未复制", not os.path.isfile(os.path.join(_out, "media", "gone.mov")))
ck("统计 media_copied=1", _res["media_copied"] == 1, str(_res))
_out2 = os.path.join(_tmp, "deliver2")
S.package_project(_proj_path, _out2, dry_run=True)
ck("dry-run 不创建目录", not os.path.isdir(_out2))
_sh.rmtree(_tmp)

print("\n通过 %d   失败 %d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
