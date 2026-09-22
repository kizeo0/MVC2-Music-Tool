@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   MVC2 Music Tool - compilador a .exe
echo ============================================
echo.

where py >nul 2>&1
if errorlevel 1 (
    echo ERROR: no se encontro "py" ^(el lanzador de Python^) en el PATH.
    echo Instala Python 3.12 desde https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Instalando/actualizando PyInstaller y Pillow...
py -3.12 -m pip install --upgrade pyinstaller pillow >nul
if errorlevel 1 (
    echo ERROR instalando dependencias. Revisa tu conexion o el PATH de Python.
    pause
    exit /b 1
)

echo Intentando instalar tkinterdnd2 ^(arrastrar y soltar, opcional^)...
py -3.12 -m pip install --upgrade tkinterdnd2 >nul 2>&1
py -3.12 -c "import tkinterdnd2" >nul 2>&1
if errorlevel 1 (
    echo   Aviso: sin tkinterdnd2, el .exe igual funciona pero sin arrastrar y soltar.
    set DNDFLAGS=
) else (
    echo   tkinterdnd2 disponible: el .exe tendra arrastrar y soltar.
    set DNDFLAGS=--hidden-import tkinterdnd2 --collect-data tkinterdnd2
)

if not exist "MVC2_MusicTool.py" (
    echo ERROR: falta MVC2_MusicTool.py en esta carpeta.
    pause
    exit /b 1
)
if not exist "ffmpeg.exe" (
    echo ERROR: falta ffmpeg.exe en esta carpeta.
    echo Es necesario para convertir MP3/FLAC/OGG/M4A.
    echo Puedes descargarlo desde https://ffmpeg.org/download.html
    pause
    exit /b 1
)
if not exist "assets" (
    echo ERROR: falta la carpeta assets en esta carpeta.
    pause
    exit /b 1
)
if exist "app.ico" (
    set ICONFLAG=--icon app.ico
    set ICODATA=--add-data "app.ico;."
) else (
    echo   Aviso: sin app.ico, el .exe usara el icono por defecto.
    set ICONFLAG=
    set ICODATA=
)

echo.
echo Limpiando compilaciones anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo Compilando (puede tardar 1-3 minutos)...
py -3.12 -m PyInstaller --onefile --noconsole --name "MVC2 Music Tool" %ICONFLAG% --add-binary "ffmpeg.exe;." --add-data "assets;assets" %ICODATA% %DNDFLAGS% MVC2_MusicTool.py --noconfirm

echo.
if exist "dist\MVC2 Music Tool.exe" (
    echo ============================================
    echo   LISTO: dist\MVC2 Music Tool.exe
    echo ============================================
) else (
    echo Algo fallo. Revisa el texto de arriba para ver el error.
)
pause
