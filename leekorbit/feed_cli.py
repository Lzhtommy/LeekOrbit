"""信息流预览 CLI（#3 验收工具）。"""
from __future__ import annotations

from . import feed


def cmd_feed(args) -> None:
    print(feed.build(args.agent, args.scene))


def register(sub) -> None:
    sp = sub.add_parser("feed", help="预览某场景的信息流")
    sp.add_argument("scene", nargs="?", default="intraday",
                    choices=["premarket", "intraday", "lunch", "close_review", "evening", "weekend"])
    sp.add_argument("--agent", default="leek-01")
    sp.set_defaults(func=cmd_feed)
