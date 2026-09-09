# -*- coding: utf-8 -*-
"""
aep_cli.py —— Adobe 工程降级命令行工具（After Effects + Premiere Pro）

支持的文件
----------
  After Effects : .aep  .aepx  .ffx
  Premiere Pro  : .prproj

按扩展名自动分派，同一个 --to 参数在两类文件上各自解释
（例如 --to 2024：AE 工程 → 24.0，PR 工程 → Version 42）。

用法示例
--------
# 查看文件信息（不改文件）
python aep_cli.py info "D:\\proj\\demo.aep"
python aep_cli.py info "D:\\proj\\demo.prproj"

# 单文件降级到 AE 2024，输出到同目录下的 AE24_0 子目录
python aep_cli.py conv "D:\\proj\\demo.aep" --to 2024

# PR 工程降级到 2024（Version 43 → 42）
python aep_cli.py conv "D:\\proj\\demo.prproj" --to 2024

# PR 工程降到"通用兼容"（Version=1，任何版本都能打开）
python aep_cli.py conv "D:\\proj\\demo.prproj" --to any

# 批量：整个目录递归，输出到指定目录，保持目录结构（AE / PR 可混在一起）
python aep_cli.py conv "D:\\proj" --to 2023 --out "D:\\out"

# 试运行，只看会改什么，不落盘
python aep_cli.py conv "D:\\proj" --to 2024 --dry-run

# 就地覆盖（危险，会先自动备份为 .bak）
python aep_cli.py conv "D:\\proj" --to 2024 --in-place

# 列出所有支持的目标版本
python aep_cli.py versions
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    from aep_core import (
        FORMAT_TABLE, SUPPORTED_EXT, convert_file, convert_paths,
        iter_files, label_of_major, risk_level, scan_file, decode_word,
    )
    from prproj_core import (
        PR_VERSION_TABLE, PR_SAFE_ANY, PR_SAFE_LABEL, SUPPORTED_EXT as PR_EXT,
        convert_prproj_file, resolve_pr_target, scan_prproj_bytes,
        short_label_of_number, label_of_number, risk_level as pr_risk_level,
    )
except ImportError:  # 允许直接双击运行时从同目录导入
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from aep_core import (
        FORMAT_TABLE, SUPPORTED_EXT, convert_file, convert_paths,
        iter_files, label_of_major, risk_level, scan_file, decode_word,
    )
    from prproj_core import (
        PR_VERSION_TABLE, PR_SAFE_ANY, PR_SAFE_LABEL, SUPPORTED_EXT as PR_EXT,
        convert_prproj_file, resolve_pr_target, scan_prproj_bytes,
        short_label_of_number, label_of_number, risk_level as pr_risk_level,
    )


ALL_EXT = tuple(SUPPORTED_EXT) + tuple(PR_EXT)
AE_EXT = tuple(SUPPORTED_EXT)


# 常用简称 -> major
ALIASES = {
    "cs6": 11.0, "11": 11.0,
    "cc": 12.0, "12": 12.0,
    "cc2014": 13.0, "2014": 13.0, "13": 13.0,
    "cc2015": 13.5, "2015": 13.5,
    "cc2017": 14.0, "2017": 14.0, "14": 14.0,
    "cc2018": 15.0, "2018": 15.0, "15": 15.0,
    "cc2019": 16.0, "2019": 16.0, "16": 16.0,
    "2020": 17.0, "17": 17.0,
    "2021": 18.0, "18": 18.0,
    "2022": 22.0, "22": 22.0,
    "2023": 23.0, "23": 23.0,
    "2024": 24.0, "24": 24.0,
    "2025": 25.0, "25": 25.0,
}


def resolve_target(s: str) -> float:
    k = s.strip().lower().replace("ae", "").strip()
    if k in ALIASES:
        return ALIASES[k]
    try:
        return float(k)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "无法识别的目标版本：%s（可用：cs6 / cc2017 / 2018 / 2020 / 2022 / 2023 / 2024 等）" % s)


def is_pr(path: str) -> bool:
    return path.lower().endswith(".prproj")


def collect_files(paths, recursive: bool = True):
    """返回 (AE/FFX/AEPX 文件列表, PRPROJ 文件列表)。"""
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
        elif recursive:
            for dp, dn, fn in os.walk(p):
                dn[:] = [x for x in dn if not x.startswith(".")]
                for n in fn:
                    if n.lower().endswith(ALL_EXT):
                        out.append(os.path.join(dp, n))
        else:
            for n in sorted(os.listdir(p)):
                fp = os.path.join(p, n)
                if os.path.isfile(fp) and n.lower().endswith(ALL_EXT):
                    out.append(fp)
    out = sorted(set(os.path.abspath(f) for f in out))
    ae = [f for f in out if f.lower().endswith(AE_EXT)]
    pr = [f for f in out if is_pr(f)]
    return ae, pr


def print_table():
    print("After Effects 目标版本（✓ = 已用 Adobe 官方素材实测验证）：")
    print("-" * 62)
    print("%-18s %-10s %-10s %s" % ("版本", "major", "格式字节", "验证"))
    print("-" * 62)
    for label, m, b, v in FORMAT_TABLE:
        print("%-18s %-10s 0x%02X       %s" % (label, "%.1f" % m, b, "✓" if v else "—"))
    print("-" * 62)
    print("注：26.x 由 22.x–25.x 的线性规律（fmt = major + 71）外推，未在本机实测。")
    print("    实际处理时须与文件内版本字解码出的 major 一致才采信，对不上就报错退出。")

    print("\nPremiere Pro 目标版本（工程格式号 Version=）：")
    print("-" * 62)
    print("%-18s %-10s %s" % ("版本", "Version", "来源"))
    print("-" * 62)
    for label, num, v in PR_VERSION_TABLE:
        print("%-18s %-10s %s" % (label, num, "文献值（未实机验证）"))
    print("%-18s %-10s %s" % (PR_SAFE_LABEL, PR_SAFE_ANY, "任何 Premiere 都能打开"))
    print("-" * 62)
    print("注：本机未安装 Premiere Pro，对照表来自 helmut4 官方文档与 Just Solve")
    print("    the File Format Problem，两者在 2018–2024 段完全一致。")
    print("    不确定时选 " + PR_SAFE_LABEL + "：不依赖对照表，最保险。")
    print("    Premiere 2026（Version 45）改用稀疏序列化，仅改版本号可能让旧版报损坏。")


def cmd_info(args):
    ae, pr = collect_files(args.paths, args.recursive)
    if not ae and not pr:
        print("未找到支持的文件（%s）" % ", ".join(ALL_EXT))
        return 1

    for f in ae:
        res = scan_file(f)
        print("\n文件：%s" % f)
        print("  类型：%s   大小：%s 字节" % (res.ftype.upper(), format(res.size, ",")))
        if not res.ok:
            print("  ✗ %s" % res.error)
            continue
        print("  源版本：%s  (%s)" % (res.src_version, label_of_major(res.src_major)))
        if res.src_word:
            d = decode_word(res.src_word)
            print("  版本字：0x%08X  build %d  平台 %s  %s"
                  % (res.src_word, d["build"], d["os_name"],
                     "Beta" if d["beta"] else "正式版"))
        print("  格式字节：0x%02X   子版本：%s"
              % (res.src_format_byte, res.extra.get("sub_ver", "-")))
        print("  需改写的标记：%d 处" % len(res.hits))
        for h in res.hits:
            print("     偏移 %-8d %-14s %s" % (h.offset, h.note,
                                                "0x%02X" % h.old_value if h.length <= 2
                                                else "0x%08X" % h.old_value))

    for f in pr:
        with open(f, "rb") as fh:
            data = fh.read()
        r = scan_prproj_bytes(data, f)
        print("\n文件：%s" % f)
        print("  类型：PRPROJ   大小：%s 字节" % format(len(data), ","))
        if not r.ok:
            print("  ✗ %s" % r.error)
            continue
        print("  源版本：Version %d  (%s)" % (r.src_number, r.src_label))
        print("  容器：%s   编码：%s"
              % ("gzip 压缩" if r.was_gzip else "未压缩的纯 XML", r.encoding))
        print("  需改写的标记：%d 处（字符偏移 %d）"
              % (len(r.hits), r.hits[0].offset if r.hits else -1))
        if r.extra.get("sparse_2026"):
            print("  ⚠ Premiere 2026 稀疏序列化：仅改版本号可能让旧版报工程损坏")
    return 0


def _pr_out_dirs(files, target, out_dir, in_place, flat):
    """模仿 aep_core.convert_paths 的输出目录规则，给 PR 文件算目标路径。"""
    base = ""
    if files:
        try:
            cp = os.path.commonpath(files)
            base = cp if os.path.isdir(cp) else os.path.dirname(cp)
        except ValueError:
            base = ""
    tag = "PR_V%d" % target
    mapping = []
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
        mapping.append((src, dst))
    return mapping


def cmd_conv(args):
    ae, pr = collect_files(args.paths, args.recursive)
    if not ae and not pr:
        print("未找到支持的文件（%s）" % ", ".join(ALL_EXT))
        return 1

    tgt_ae = None
    tgt_pr = None
    if ae:
        tgt_ae = resolve_target(args.to)
    if pr:
        try:
            tgt_pr = resolve_pr_target(args.to)
        except ValueError as e:
            print("PR 工程的目标版本解析失败：%s" % e)
            print("（Premiere 工程请用 2024 / 2023 / 2022 / any 这样的写法）")
            return 1

    all_files = ae + pr
    print("待处理：%d 个文件（AE %d / PR %d）%s"
          % (len(all_files), len(ae), len(pr),
             "（试运行，不写盘）" if args.dry_run else ""))
    if ae:
        print("  After Effects 目标：%s" % label_of_major(tgt_ae))
    if pr:
        print("  Premiere Pro  目标：Version %d (%s)"
              % (tgt_pr, short_label_of_number(tgt_pr)))
    print()

    if args.in_place:
        for f in all_files:
            bak = f + ".bak"
            if not os.path.exists(bak):
                with open(f, "rb") as a, open(bak, "wb") as b:
                    b.write(a.read())
        print("已为每个文件生成 .bak 备份\n")

    results = []
    if ae:
        results.extend(convert_paths(ae, tgt_ae, out_dir=args.out or "",
                                     in_place=args.in_place, recursive=args.recursive,
                                     aggressive=args.aggressive, dry_run=args.dry_run,
                                     flat=args.flat))
    if pr:
        for src, dst in _pr_out_dirs(pr, tgt_pr, args.out or "",
                                     args.in_place, args.flat):
            results.append(convert_prproj_file(src, dst, tgt_pr,
                                               dry_run=args.dry_run))

    n_ok = n_skip = n_fail = 0
    for r in results:
        name = os.path.basename(r.path)
        if r.status == "converted":
            n_ok += 1
            print("  [改写] %-42s %s -> %s  (%s)"
                  % (name[:42], r.src_version, r.dst_version, r.message))
            if r.out_path:
                print("         -> %s" % r.out_path)
        elif r.status == "already_lower":
            n_skip += 1
            print("  [跳过] %-42s %s" % (name[:42], r.message))
        else:
            n_fail += 1
            print("  [失败] %-42s %s" % (name[:42], r.message))

    print("\n" + "=" * 66)
    print("完成：改写 %d   跳过 %d   失败 %d" % (n_ok, n_skip, n_fail))
    if n_ok and not args.dry_run:
        print("\n⚠ 本工具只改版本标记，让旧版软件愿意加载文件，不做真正的格式转换。")
        print("  旧版本不支持的效果 / 属性 / 表达式 / 第三方插件仍会丢失或报错。")
        print("  请务必用目标版本的 AE / PR 实机打开确认，原文件未被改动。")
    print("=" * 66)
    return 0 if n_fail == 0 else 2


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="aep_cli",
        description="Adobe 工程降级工具（AE 的 .aep/.aepx/.ffx + PR 的 .prproj，无需安装 Adobe 软件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例： python aep_cli.py conv D:\\proj --to 2024 --out D:\\out")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("info", help="查看文件版本信息（不修改）")
    p1.add_argument("paths", nargs="+", help="文件或目录")
    p1.add_argument("--no-recursive", dest="recursive", action="store_false")
    p1.set_defaults(recursive=True, func=cmd_info)

    p2 = sub.add_parser("conv", help="执行降级")
    p2.add_argument("paths", nargs="+", help="文件或目录")
    p2.add_argument("--to", "-t", required=True,
                    help="目标版本：AE 用 2024 / cc2018 / 22；PR 用 2024 / 2023 / any")
    p2.add_argument("--out", "-o", default="", help="输出目录（默认在源目录旁建子目录）")
    p2.add_argument("--in-place", action="store_true", help="就地覆盖（自动先备份 .bak）")
    p2.add_argument("--flat", action="store_true", help="输出时不保留子目录结构")
    p2.add_argument("--aggressive", action="store_true",
                    help="[仅 AE] 激进模式：把 minor/patch/build 一并归零")
    p2.add_argument("--dry-run", "-n", action="store_true", help="试运行，不写盘")
    p2.add_argument("--no-recursive", dest="recursive", action="store_false")
    p2.set_defaults(recursive=True, func=cmd_conv)

    p3 = sub.add_parser("versions", help="列出支持的目标版本")
    p3.set_defaults(func=lambda a: (print_table(), 0)[1])

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
