# -*- coding: utf-8 -*-
"""collector.selfdestruct 自毁模块测试:plain assert,直接 `python tests/test_selfdestruct.py`。

用临时目录验证 _clear 的删除逻辑;并用 monkeypatch 验证 _activation_gate 的
触发条件(3 次输错触发 / 取消不触发)与 self_destruct 非 frozen 不删文件。
"""
import datetime
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import selfdestruct as sd


def _make_root():
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / "data" / "a.db").write_text("x")
    (root / "logs").mkdir()
    (root / "logs" / "m.log").write_text("z")
    (root / "output").mkdir()
    (root / "output" / "p.csv").write_text("w")
    (root / "config.yaml").write_text("secret")
    return root


def _test_clear_deletes_all():
    root = _make_root()
    sd._clear(root)
    assert not (root / "data").exists()
    assert not (root / "logs").exists()
    assert not (root / "output").exists()
    assert not (root / "config.yaml").exists()
    print("test_clear_deletes_all OK")


def _test_clear_preserves_unrelated_files():
    root = _make_root()
    (root / "无关文件.txt").write_text("keep")
    sd._clear(root)
    assert (root / "无关文件.txt").exists(), "用户同目录无关文件应保留"
    print("test_clear_preserves_unrelated_files OK")


def _test_self_destruct_source_mode_does_not_delete():
    """源码(非 frozen)运行时 self_destruct 不应删真实项目文件。"""
    import collector.selfdestruct as c
    # 强制走非 frozen 分支;self_destruct 记 warning 并返回,不删文件。
    had_frozen = hasattr(sys, "frozen")
    saved_frozen = getattr(sys, "frozen", None)
    sys.frozen = False
    try:
        c.self_destruct()  # 不抛错即可;不应删 config.yaml(源码模式)
        from pathlib import Path as P
        assert P("config.yaml").exists(), "源码模式不应删除 config.yaml"
    finally:
        if had_frozen:
            sys.frozen = saved_frozen
        else:
            del sys.frozen
    print("test_self_destruct_source_mode_does_not_delete OK")


def _test_gate_trigger_source_mode():
    """3 次输错触发 self_destruct(非 frozen 下记日志不删,验证调用链)。"""
    import manager as mgr
    from collector import license as L
    import tkinter.simpledialog as sd

    _orig_showerror = mgr.messagebox.showerror
    _orig_ask = sd.askstring
    L._date_provider = lambda: datetime.date(2026, 10, 1)  # 到期
    mgr.messagebox.showerror = lambda *a, **k: None
    calls = {"n": 0}
    sd.askstring = lambda *a, **kw: "AUTOWFM-BADKEY"  # 3 次都输错
    import collector.selfdestruct as sdm
    orig = sdm.self_destruct
    sdm.self_destruct = lambda: calls.__setitem__("n", calls["n"] + 1)
    try:
        mgr._activation_gate()
        assert calls["n"] == 1, f"3 次输错应触发自毁,实际 {calls['n']}"
    finally:
        sdm.self_destruct = orig
        mgr.messagebox.showerror = _orig_showerror
        sd.askstring = _orig_ask
        L._date_provider = datetime.date.today
    print("test_gate_trigger_source_mode OK")


def _test_gate_cancel_no_trigger():
    """用户取消不触发自毁。"""
    import manager as mgr
    from collector import license as L
    import tkinter.simpledialog as sd

    _orig_showerror = mgr.messagebox.showerror
    _orig_ask = sd.askstring
    L._date_provider = lambda: datetime.date(2026, 10, 1)
    mgr.messagebox.showerror = lambda *a, **k: None
    calls = {"n": 0}
    sd.askstring = lambda *a, **kw: None  # 用户取消
    import collector.selfdestruct as sdm
    orig = sdm.self_destruct
    sdm.self_destruct = lambda: calls.__setitem__("n", calls["n"] + 1)
    try:
        mgr._activation_gate()
        assert calls["n"] == 0, "取消不应触发自毁"
    finally:
        sdm.self_destruct = orig
        mgr.messagebox.showerror = _orig_showerror
        sd.askstring = _orig_ask
        L._date_provider = datetime.date.today
    print("test_gate_cancel_no_trigger OK")


def main():
    _test_clear_deletes_all()
    _test_clear_preserves_unrelated_files()
    _test_self_destruct_source_mode_does_not_delete()
    _test_gate_trigger_source_mode()
    _test_gate_cancel_no_trigger()
    print("ALL selfdestruct tests OK")


if __name__ == "__main__":
    main()