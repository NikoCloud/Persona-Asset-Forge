@echo off
setlocal enabledelayedexpansion
echo ============================================
echo   PNG Background Remover - Build Script
echo ============================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.10+ from https://www.python.org and make sure to check
    echo "Add Python to PATH" during installation.
    pause
    exit /b 1
)
echo Python found:
python --version
echo.

:: Install / update dependencies
echo Installing dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install pillow customtkinter darkdetect pyinstaller
if errorlevel 1 (
    echo ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo.

:: Clean previous build artifacts
echo Cleaning previous build...
if exist dist   rmdir /s /q dist
if exist build  rmdir /s /q build
echo Done.
echo.

:: Build the EXE
echo Building BGRemover.exe (this takes 60-120 seconds)...
echo.
python -m PyInstaller bg_remover.spec
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed. See output above for details.
    pause
    exit /b 1
)

:: Report result
echo.
if exist "dist\BGRemover.exe" (
    echo ============================================
    echo   BUILD SUCCESSFUL
    echo ============================================
    for %%A in ("dist\BGRemover.exe") do (
        set /a SIZE_MB=%%~zA / 1048576
        echo   Output : dist\BGRemover.exe
        echo   Size   : !SIZE_MB! MB
    )
    echo.
    echo You can now copy dist\BGRemover.exe anywhere and run it standalone.
) else (
    echo ERROR: EXE not found after build. Check PyInstaller output above.
    pause
    exit /b 1
)

echo.
pause
