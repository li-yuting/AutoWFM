# -*- coding: utf-8 -*-
"""一键打包 AutoWFM 为文件夹形态 exe (PyInstaller + PyArmor 混淆)。

流程:
1. 用 PyArmor 混淆 collector/license.py + license_public_key.py 到临时目录。
2. 把混淆产物覆盖到 collector/ 包,并把 pyarmor_runtime_* 放到项目根(供顶层导入)。
3. 运行 PyInstaller 打包 AutoWFM.exe(manager 入口,onedir)。
4. 恢复被覆盖的原始 license 文件(保持源码可读)。

前置依赖(开发机,非运行时):
    .venv\\Scripts\\pip install pyinstaller pyarmor cryptography

用法:
    python tools/build_exe.py          # 完整打包(混淆+PyInstaller)
    python tools/build_exe.py --skip-obfuscate   # 复用已有混淆产物只打包
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECA = ROOT / "build" / "autowfm.spec"
OBF_DIR = ROOT / "build" / "_pyarmor_license"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _run(cmd: list[str]) -> None:
    print(">> " + " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"命令失败: {' '.join(cmd)}")


def _obfuscate() -> None:
    """混淆 license 模块到 build/_pyarmor_license。"""
    if OBF_DIR.exists():
        shutil.rmtree(OBF_DIR)
    OBF_DIR.mkdir(parents=True)
    _run([
        ROOT / ".venv" / "Scripts" / "pyarmor.exe", "gen",
        "-O", str(OBF_DIR),
        str(ROOT / "collector" / "license.py"),
        str(ROOT / "collector" / "license_public_key.py"),
    ])


def _overlay() -> None:
    """把混淆版覆盖到 collector 包,并放 runtime 到项目根。

    覆盖前备份原始文件为 build/<name>.orig,供 _restore 中途失败时恢复。
    """
    for name in ("license.py", "license_public_key.py"):
        src = ROOT / "collector" / name
        orig = ROOT / "build" / f"{name}.orig"
        # 覆盖前总是把当前(原始)源码备份到 .orig,确保中途失败/恢复用的是最新源码。
        shutil.copy(src, orig)
        shutil.copy(OBF_DIR / name, src)
    # 把 pyarmor_runtime_* 复制到项目根(顶层 sys.path 可导入)
    for d in OBF_DIR.iterdir():
        if d.is_dir() and d.name.startswith("pyarmor_runtime"):
            target = ROOT / d.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(d, target)
    print("混淆版已覆盖到 collector/,runtime 已就位,原始文件已备份到 build/*.orig。")


def _restore() -> None:
    """恢复原始 license 文件,删除项目根的 runtime。"""
    for name in ("license.py", "license_public_key.py"):
        orig = ROOT / "build" / f"{name}.orig"
        if orig.exists():
            shutil.copy(orig, ROOT / "collector" / name)
    for d in ROOT.iterdir():
        if d.is_dir() and d.name.startswith("pyarmor_runtime"):
            shutil.rmtree(d)
    print("已恢复原始 license 文件。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-obfuscate", action="store_true",
                    help="跳过 PyArmor(复用已有 build/_pyarmor_license)")
    args = ap.parse_args()

    if not (ROOT / ".venv" / "Scripts" / "pyinstaller.exe").exists():
        sys.exit("未安装 PyInstaller,请先: .venv/Scripts/pip install pyinstaller")
    if not args.skip_obfuscate:
        _obfuscate()
    else:
        print("跳过混淆(复用已有产物)。")
    try:
        _overlay()
        _run([PYTHON, ROOT / ".venv" / "Scripts" / "pyinstaller.exe",
              "--noconfirm", "--clean", str(SPECA)])
    finally:
        _restore()
    print("打包完成: dist/AutoWFM/")


if __name__ == "__main__":
    main()