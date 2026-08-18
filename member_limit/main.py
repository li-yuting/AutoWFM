# -*- coding: utf-8 -*-
"""CLI 入口：python -m member_limit.main [--limit N] [--dry-run]。

脱离 GUI 运行同一逻辑；--dry-run 只读当前值不修改。
"""
from __future__ import annotations

import argparse
import sys

from member_limit import core
from member_limit.config import ConfigError, load as load_config


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="腾讯云联络中心成员接待上限批量修改")
    ap.add_argument("--limit", type=int, default=None,
                    help="目标接待上限（缺省用 config.yaml 的 member_limit.limit）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只检查当前值，不实际修改")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2
    if args.limit is not None:
        config["limit"] = args.limit
    try:
        core.run_member_limit(config, progress_cb=print, dry_run=args.dry_run)
    except Exception as exc:
        print(f"执行失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
