# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for PNG Background Remover.
# Use a spec file (not the CLI) so we can correctly collect customtkinter assets.
#
# Build with:  python -m PyInstaller bg_remover.spec
#

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect customtkinter theme JSON files, fonts, and icons
ctk_datas = collect_data_files('customtkinter')
darkdetect_datas = collect_data_files('darkdetect')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=ctk_datas + darkdetect_datas,
    hiddenimports=(
        collect_submodules('customtkinter') +
        [
            'darkdetect',
            # Pillow
            'PIL._imagingtk',       # C extension needed by ImageTk
            'PIL.ImageTk',
            'PIL.Image',
            'PIL.ImageDraw',
            'PIL.ImageChops',
            'PIL.PngImagePlugin',   # PNG read/write support
            'PIL.JpegImagePlugin',  # JPEG read support
            # Tkinter
            'tkinter',
            'tkinter.filedialog',
            'tkinter.messagebox',
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavyweight scientific stack — not needed
        'numpy',
        'scipy',
        'matplotlib',
        'pandas',
        # Qt GUI backends
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        # Jupyter / IPython
        'IPython', 'jupyter', 'notebook',
        # Unused PIL plugins (keep PNG + JPEG; exclude everything else)
        'PIL.BmpImagePlugin',
        'PIL.GifImagePlugin',
        'PIL.Jpeg2KImagePlugin',
        'PIL.TiffImagePlugin',
        'PIL.WebPImagePlugin',
        'PIL.IcoImagePlugin',
        'PIL.PsdImagePlugin',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BGRemover',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # Compress with UPX if available (saves ~30%)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # No terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # Replace with path to a .ico file if desired
)
