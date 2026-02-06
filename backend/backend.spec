# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the braille-visualizer backend.
# Build with:  pyinstaller backend/backend.spec

import os

block_cipher = None
backend_dir = os.path.join(SPECPATH)
root_dir = os.path.dirname(backend_dir)

a = Analysis(
    [os.path.join(backend_dir, 'main.py')],
    pathex=[backend_dir],
    binaries=[],
    datas=[
        # Bundle the frontend folder so FastAPI can serve it
        (os.path.join(root_dir, 'frontend'), 'frontend'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='backend',
)
