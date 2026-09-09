# -*- coding: utf-8 -*-
"""
aep_cli.py —— AEP / FFX / AEPX 降级命令行工具

用法示例
--------
# 查看文件信息（不改文件）
python aep_cli.py info "D:\\proj\\demo.aep"

# 单文件降级到 AE 2024，输出到同目录下的 AE24_0 子目录
python aep_cli.py conv "D:\\proj\\demo.aep" --to 2024

# 批量：整个目录递归，输出到指定目录，保持目录结构
python aep_cli.py conv "D:\\proj" --to 2023 --out "D:\\out"

# 批量：当前目录下的所有预设
python aep_cli.py conv "D:\\presets" --to 2022

# 试运行，只看会改什么，不落盘
python aep_cli.py conv "D:\\proj" --to 2024 --dry-run

# 就地覆盖（危险，会先自动备份为 .bak）
python aep_cli.py conv "D:\\proj" --to 2024 --in-place
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
except ImportError:  # 允许直接双击运行时从同目录导入
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from aep_core import (
        FORMAT_TABLE, SUPPORTED_EXT, convert_file, convert_paths,
        iter_files, label_of_major, risk_level, scan_file, decode_word,
    )


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


def print_table():
    print("支持的目标版本（✓ = 已用 Adobe 官方素材实测验证）：")
    print("-" * 62)
    print("%-18s %-10s %-10s %s" % ("版本", "major", "格式字节", "验证"))
    print("-" * 62)
    for label, m, b, v in FORMAT_TABLE:
        print("%-18s %-10s 0x%02X       %s" % (label, "%.1f" % m, b, "✓" if v else "—"))
    print("-" * 62)
    print("注：26.x 由 22.x–25.x 的线性规律（fmt = major + 71）外推，未在本机实测。")
    print("    实际处理时须与文件内版本字解码出的 major 一致才采信，对不上就报错退出。")


def cmd_info(args):
    files = []
    for p in args.paths:
        files.extend(iter_files(p, args.recursive))
    if not files:
        print("未找到支持的文件（%s）" % ", ".join(SUPPORTED_EXT))
        return 1
    for f in sorted(set(files)):
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
    return 0


def cmd_conv(args):
    target = resolve_target(args.to)
    files = []
    for p in args.paths:
        files.extend(iter_files(p, args.recursive))
    files = sorted(set(os.path.abspath(f) for f in files))
    if not files:
        print("未找到支持的文件（%s）" % ", ".join(SUPPORTED_EXT))
        return 1

    print("目标版本：%s" % label_of_major(target))
    print("待处理：%d 个文件%s\n" % (len(files), "（试运行，不写盘）" if args.dry_run else ""))

    if args.in_place:
        for f in files:
            bak = f + ".bak"
            if not os.path.exists(bak):
                with open(f, "rb") as a, open(bak, "wb") as b:
                    b.write(a.read())
        print("已为每个文件生成 .bak 备份\n")

    results = convert_paths(files, target, out_dir=args.out or "",
                            in_place=args.in_place, recursive=args.recursive,
                            aggressive=args.aggressive, dry_run=args.dry_run,
                            flat=args.flat)

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
        print("\n⚠ 本工具只改版本标记，让旧版 AE 愿意加载文件，不做真正的格式转换。")
        print("  旧版本不支持的效果 / 属性 / 表达式 / 第三方插件仍会丢失或报错。")
        print("  请务必用目标版本 AE 实机打开确认，原文件未被改动。")
    print("=" * 66)
    return 0 if n_fail == 0 else 2


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="aep_cli",
        description="AE 工程/预设降级工具（无需安装 After Effects）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例： python aep_cli.py conv D:\\proj --to 2024 --out D:\\out")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("info", help="查看文件版本信息（不修改）")
    p1.add_argument("paths", nargs="+", help="文件或目录")
    p1.add_argument("--no-recursive", dest="recursive", action="store_false")
    p1.set_defaults(recursive=True, func=cmd_info)

    p2 = sub.add_parser("conv", help="执行降级")
    p2.add_argument("paths", nargs="+", help="文件或目录")
    p2.add_argument("--to", "-t", required=True, help="目标版本，如 2024 / cc2018 / 22")
    p2.add_argument("--out", "-o", default="", help="输出目录（默认在源目录旁建子目录）")
    p2.add_argument("--in-place", action="store_true", help="就地覆盖（自动先备份 .bak）")
    p2.add_argument("--flat", action="store_true", help="输出时不保留子目录结构")
    p2.add_argument("--aggressive", action="store_true",
                    help="激进模式：把 minor/patch/build 一并归零（默认只改主版本）")
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
