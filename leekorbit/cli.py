"""LeekOrbit 命令行入口。"""
from __future__ import annotations

import argparse
import logging

from . import db


def cmd_wakes(args) -> None:
    for r in db.rows(
        "SELECT ts, agent, scene, status, detail FROM wakes ORDER BY ts DESC LIMIT ?", args.limit
    ):
        print(f"{r['ts']}  {r['agent']:<10} {r['scene']:<14} {r['status']:<8} {r['detail'] or ''}")


def cmd_run(args) -> None:
    from .daemon import run_daemon

    run_daemon()


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="leekorbit")
    p.add_argument("--db", default=None, help="SQLite 路径（默认 data/leekorbit.db）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("wakes", help="列出唤醒记录")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_wakes)

    sp = sub.add_parser("run", help="常驻运行（心跳 + 仪表盘）")
    sp.set_defaults(func=cmd_run)

    from . import broker_cli, feed_cli, wake_cli

    broker_cli.register(sub)
    feed_cli.register(sub)
    wake_cli.register(sub)

    args = p.parse_args(argv)
    if args.db:
        db.configure(args.db)
    args.func(args)


if __name__ == "__main__":
    main()
