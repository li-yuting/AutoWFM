from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path

from peakflow import config


def latest_download_dir(root: Path = config.AUTO_TABLEAU_DIR) -> Path:
    dl = Path(root) / "downloads"
    if not dl.is_dir():
        raise FileNotFoundError(f"AutoTableau 下载目录不存在: {dl}")
    dirs = [d for d in dl.iterdir()
            if d.is_dir() and d.name.replace("-", "").isdigit()]
    if not dirs:
        raise FileNotFoundError(f"AutoTableau downloads 下没有日期目录: {dl}")
    dirs.sort(key=lambda p: p.name, reverse=True)
    return dirs[0]


def sync_from_autotableau(root: Path = config.AUTO_TABLEAU_DIR,
                          dest: Path = config.DATA_DIR,
                          files=None) -> Path:
    files = list(files) if files is not None else list(config.FETCH_FILES)
    src = latest_download_dir(root)
    age = (dt.date.today() - dt.date.fromisoformat(src.name)).days
    if age > config.FETCH_MAX_AGE_DAYS:
        raise ValueError(
            f"AutoTableau 最新下载目录过旧({src.name}，{age} 天前)。"
            "请先运行 AutoTableau 的 run_now.bat 或等待定时任务。")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in files:
        f = src / name
        if not f.is_file():
            raise FileNotFoundError(f"AutoTableau 最新下载中缺少 {name}（目录 {src}）")
        shutil.copy2(f, dest / name)
    return src
