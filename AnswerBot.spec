# -*- mode: python ; coding: utf-8 -*-
# AnswerBot.spec — PyInstaller 打包配置（单文件、无控制台窗口）
#
# 用法（在项目根目录）：
#   pyinstaller AnswerBot.spec
# 产出：dist/AnswerBot.exe
#
# 若杀软误报，可改用 --onedir 模式（误报率更低）：
#   把 console=False 保留，onefile 改 onedir 需要在 EXE 中不传 a.binaries/a.datas
#   并增加 COLLECT 步骤；README「打包」节有说明。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # pywin32：win32ui 等动态加载模块
        'win32api', 'win32con', 'win32gui', 'win32ui',
        # pynput：平台后端动态导入
        'pynput.mouse', 'pynput.keyboard',
        'pynput.mouse._win32', 'pynput.keyboard._win32',
        # keyboard / mss / pystray
        'keyboard', 'mss', 'pystray',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AnswerBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                # -w：无控制台窗口（NFR-03）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
