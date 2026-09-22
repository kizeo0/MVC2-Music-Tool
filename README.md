# MVC2 Music Tool

Herramienta con interfaz grafica para **escuchar y reemplazar** la musica de
**Marvel vs. Capcom 2 (PS3)**. Cada tema del juego es un `adx_*.bin` con un
stream CRI ADX (estereo, 44100 Hz); el programa lo convierte, lo re-codifica
y lo deja listo para copiar de vuelta al juego.

Ver `canciones.txt` para saber que `.bin` es cada cancion.

<img width="904" height="633" alt="image" src="https://github.com/user-attachments/assets/71290a50-5a30-407f-a9a4-be44423e33a4" />

## Uso rapido

1. Abri `MVC2 Music Tool.exe` (o `MVC2_MusicTool.py`).
2. **Cargar carpeta...** y elegi el `gdrom` del juego (o arrastra la carpeta
   sobre la ventana). Al cargar se preparan los temas con barra de progreso.
3. Doble clic (o **Reproducir**) para escuchar cada tema.
4. Elegi el **modo** (Libre / Estricto / Hibrido) y usa
   **Cargar reemplazo...** con tu audio (`.wav`, `.mp3`, `.flac`, `.ogg`,
   `.m4a`, `.adx`).
5. **Guardar todo...** en una carpeta y copia los `.bin` al juego.

## Modos

- **ESTRICTO**: el `.bin` nuevo pesa exactamente lo mismo que el original
  (lo que la PS3 necesita). Si tu audio no entra, se abre el editor para
  recortarlo.
- **LIBRE**: el `.bin` puede crecer. La PS3 puede no aceptarlo: prueba
  primero en RPCS3.
- **HIBRIDO**: clic en el casillero de cada fila para marcar ese tema como
  estricto (rojo) o libre (verde).

El selector de **Loop** define si tu reemplazo repite (todo, seleccion del
editor) o suena una sola vez.

## Requisitos

- **Windows**. Si usas el `.exe`: nada mas (ya trae todo adentro, incluido
  `ffmpeg`).
- Si corres el codigo fuente: Python 3.12, `ffmpeg.exe` junto al `.py`
  (solo para `.mp3`/`.flac`/`.ogg`/`.m4a`; los `.wav` andan sin nada),
  opcionalmente `Pillow` y `tkinterdnd2` (`pip install pillow tkinterdnd2`).

## Compilar tu propio .exe

Doble clic en `compilar_music.bat` (necesita `ffmpeg.exe`, `app.ico` y la
carpeta `assets/` al lado). El resultado queda en `dist/`.

## Licencia

No comercial para modding, dado que trabaja con archivos protegidos de un
juego de Capcom.
