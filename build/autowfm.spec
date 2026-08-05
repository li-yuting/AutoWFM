# -*- coding: utf-8 -*-
"""AutoWFM 管理器 exe 打包 spec (PyInstaller)。用法见 tools/build_exe.py。

注意:打包前需先运行 PyArmor 混淆 collector/license.py,并把混淆产物(pyarmor_runtime_*)放到项目根。
templates/config 等资源通过 datas 打进 _internal。
"""
from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path

# 用 spec 文件位置推导项目根(spec 在 <root>/build/,故取父目录的父目录),避免硬编码绝对路径。
ROOT = str(Path(SPEC).resolve().parent.parent)

hiddenimports = (
    collect_submodules('cryptography')
    + ['collector.license', 'collector.license_public_key', 'collector.selfdestruct']
    + collect_submodules('APScheduler')
    + ['websocket', 'flask', 'pandas', 'numpy', 'statsmodels', 'openpyxl',
       'pystray', 'PIL', 'requests', 'yaml']
)

datas = [
    (ROOT + r'\config.yaml', '.'),
    (ROOT + r'\holidays.txt', '.'),
    (ROOT + r'\ico.ico', '.'),
    (ROOT + r'\dashboard\templates', r'dashboard\templates'),
]

a = Analysis(
    [ROOT + r'\manager.py'],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tests', 'archive', 'writeforecast'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoWFM',
    debug=False,
    upx=True,
    console=False,
    icon=ROOT + r'\ico.ico',
)
# console=False 时 onedir 模式仍需 COLLECT
coll = COLLECT(exe, a.binaries, a.datas, name='AutoWFM')