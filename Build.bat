@echo off
chcp 65001 >nul
REM ============================================================
REM  Fingertip - Nuitka 打包脚本（standalone 非单文件模式）
REM  用法: 在项目根目录直接运行 Build.bat
REM  输出: build\main.dist\main.exe
REM ============================================================

cd /d "%~dp0"

echo [1/2] 检查 Nuitka ...
python -m nuitka --version >nul 2>&1
if errorlevel 1 (
    echo Nuitka 未安装，请先执行: pip install nuitka
    pause
    exit /b 1
)

echo [2/2] 开始打包 ...
python -m nuitka ^
    --standalone ^
    --enable-plugin=pyqt5 ^
    --windows-console-mode=disable ^
    --windows-icon-from-ico=app\static\logo.ico ^
    --company-name="Fingertip" ^
    --product-name="Fingertip" ^
    --file-version=1.0.0 ^
    --product-version=1.0.0 ^
    --file-description="Fingertip 智能指环上位机" ^
    --include-package=app ^
    --include-package=pyqtgraph ^
    --include-package=qfluentwidgets ^
    --include-package=bleak ^
    --include-package=hmmlearn ^
    --include-data-dir=app\common\qss=app\common\qss ^
    --include-data-dir=app\static=app\static ^
    --include-data-dir=app\hmm_gesture\pretrained_models=app\hmm_gesture\pretrained_models ^
    --include-data-dir=app\hmm_gesture\sample_data=app\hmm_gesture\sample_data ^
    --include-data-dir=app\hmm_gesture\models=app\hmm_gesture\models ^
    --include-data-dir=app\hmm_gesture\gesture_data=app\hmm_gesture\gesture_data ^
    --output-dir=build ^
    --assume-yes-for-downloads ^
    --show-progress ^
    main.py

if errorlevel 1 (
    echo.
    echo 打包失败，请检查上方错误信息。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo 打包完成! 可执行文件位于: build\main.dist\main.exe
echo ============================================================
pause
