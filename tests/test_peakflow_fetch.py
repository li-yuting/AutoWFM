import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime as dt
import shutil
from pathlib import Path

from peakflow import fetch

# Use workspace-local temp to avoid sandbox restrictions on system temp
_WS_TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".test_tmp")


def _make_autotableau(tmp_dir, date_str=None):
    if date_str is None:
        date_str = dt.date.today().isoformat()
    root = Path(tmp_dir) / "AutoTableau"
    dl = root / "downloads" / date_str
    dl.mkdir(parents=True)
    (dl / "在线各类用户.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (dl / "热线各类用户.csv").write_text("a,b\n3,4\n", encoding="utf-8")
    return root


def test_latest_download_dir_picks_newest():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        today = dt.date.today().isoformat()
        root = _make_autotableau(_WS_TMP, yesterday)
        _make_autotableau(_WS_TMP, today)
        assert fetch.latest_download_dir(root).name == today
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_latest_download_dir_picks_newest")


def test_sync_copies_files():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        root = _make_autotableau(_WS_TMP)  # today's date
        dest = Path(_WS_TMP) / "data"
        src = fetch.sync_from_autotableau(root, dest, files=["在线各类用户.csv", "热线各类用户.csv"])
        assert src.name == dt.date.today().isoformat()
        assert (dest / "在线各类用户.csv").exists()
        assert (dest / "热线各类用户.csv").read_text(encoding="utf-8") == "a,b\n3,4\n"
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_sync_copies_files")


def test_sync_missing_file_raises():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        root = _make_autotableau(_WS_TMP)
        try:
            fetch.sync_from_autotableau(root, Path(_WS_TMP) / "data", files=["不存在的.csv"])
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError as e:
            assert "缺少" in str(e)
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_sync_missing_file_raises")


def test_sync_too_old_raises():
    os.makedirs(_WS_TMP, exist_ok=True)
    try:
        old = (dt.date.today() - dt.timedelta(days=10)).isoformat()
        root = _make_autotableau(_WS_TMP, old)
        try:
            fetch.sync_from_autotableau(root, Path(_WS_TMP) / "data")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "过旧" in str(e)
    finally:
        shutil.rmtree(_WS_TMP, ignore_errors=True)
    print("PASS test_sync_too_old_raises")


def main():
    test_latest_download_dir_picks_newest()
    test_sync_copies_files()
    test_sync_missing_file_raises()
    test_sync_too_old_raises()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()