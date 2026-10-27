"""CLI for repeatable local parsing and acceptance checks."""

import argparse
import json
import sys

import polars as pl

from cs2ta.analysis import events_for_round, kill_summary
from cs2ta.pipeline import ingest, load_dataset, parse_round_positions
from cs2ta.radar import download_radars


def main() -> int:
    parser = argparse.ArgumentParser(description="CS2 本地 Demo 分析 MVP")
    parser.add_argument("demo", nargs="?", help="本地 CS2 .dem 文件路径")
    parser.add_argument("--download-maps", action="store_true", help="下载并校验地图底图到本地缓存")
    parser.add_argument("--data-dir", default=None, help="本地缓存目录，默认使用当前环境配置（不要提交 Git）")
    parser.add_argument("--round", type=int, dest="round_id", help="额外解析指定回合的轨迹")
    parser.add_argument("--tick-step", type=int, default=16)
    args = parser.parse_args()
    if not args.demo and not args.download_maps:
        parser.error("请提供 Demo 路径，或使用 --download-maps 下载地图。")
    try:
        if args.download_maps:
            paths = download_radars()
            print(json.dumps({"map_images": [str(path.resolve()) for path in paths]}, ensure_ascii=False, indent=2))
            if not args.demo:
                return 0
        destination, manifest = ingest(args.demo, args.data_dir)
        events, rounds = load_dataset(destination)
        output = {
            "dataset": str(destination), "map": manifest["header"].get("map_name", "unknown"),
            "rounds": len(rounds), "events": len(events),
            "warnings": manifest["warnings"],
        }
        if args.round_id is not None:
            states = parse_round_positions(args.demo, destination, args.round_id, args.tick_step)
            row = rounds.filter(pl.col("round_id") == args.round_id).row(0, named=True)
            output["selected_round"] = {
                "round_id": args.round_id, "position_rows": len(states),
                "sample_ticks": int(states["tick"].n_unique()), "players": int(states["steamid"].n_unique()),
                "kills": kill_summary(events_for_round(events, row)).to_dicts(),
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"解析失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
