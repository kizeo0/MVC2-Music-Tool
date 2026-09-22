# -*- coding: utf-8 -*-
"""MVC2 Music Tool - reemplazo simple de la musica de Marvel vs Capcom 2 (PS3).

Cada tema del juego es un archivo adx_*.bin con UN solo stream CRI ADX
(estereo, 44100 Hz). Esta herramienta:

- Carga una carpeta (el gdrom con los adx_*.bin) o un .bin suelto y lista
  los temas con su nombre, duracion, loop y tamano.
- Reproduce cada tema (doble clic o boton Reproducir).
- Reemplaza un tema por tu propio audio (.wav, .mp3, .flac, .ogg, .m4a,
  .adx): convierte solo a ADX estereo 44100 Hz con loop a eleccion.
- Tres modos, igual que la herramienta de doblaje:
    LIBRE: el .bin nuevo puede pesar lo que sea.
    ESTRICTO: el .bin nuevo pesa EXACTAMENTE lo mismo que el original
      (lo que la PS3 necesita). Si tu audio no entra, se abre el editor
      para recortarlo antes de aceptar el reemplazo.
    HIBRIDO: clic en el casillero de cada fila para marcar ese tema
      como estricto (rojo) o libre (verde).
- Editor de audio integrado (recortar, fades, normalizar, zoom,
  seleccion; boton "Loop = seleccion" para loopear solo un tramo).
- Guarda los temas reemplazados en una carpeta de salida con el MISMO
  nombre de archivo, listos para copiar al juego.

Requisitos (igual que la herramienta de doblaje):
- Windows (usa winsound para reproducir).
- ffmpeg.exe junto al .py/.exe SOLO si quieres usar mp3/flac/ogg/m4a.
  Los .wav funcionan sin nada extra.
- Opcional: tkinterdnd2 para arrastrar y soltar (pip install tkinterdnd2).
- Opcional: assets/verde.png y assets/rojo.png para los iconos del
  modo hibrido (si faltan, el modo hibrido igual funciona, sin iconos).

Linea de comandos:
  py MVC2_MusicTool.py info <archivo.bin>
  py MVC2_MusicTool.py extraer_wav <archivo.bin> [salida.wav]
"""

import os
import sys
import math
import wave
import array
import struct
import tempfile
import threading
import subprocess
import shutil

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
    import tkinter.ttk as ttk
    TK_OK = True
except Exception:
    TK_OK = False

try:
    import winsound
except ImportError:
    winsound = None

HAVE_DND = False
if TK_OK:
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        HAVE_DND = True
    except ImportError:
        pass

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


# ======================================================================
# RUTAS
# ======================================================================

def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts):
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def safe_print(msg):
    try:
        print(msg)
    except Exception:
        pass


# ======================================================================
# NOMBRES DE LOS TEMAS
# (los marcados con (?) son por duracion/loop y conviene confirmarlos
#  de oido con el boton Reproducir; la tabla esta al principio del
#  archivo a proposito para editarla facil)
# ======================================================================

TRACK_NAMES = {
    'adx_capl': 'Logo Capcom',
    'adx_cont': 'Continue',
    'adx_here': 'Here Comes a New Challenger',
    'adx_menu': 'Menu principal',
    'adx_netw': 'Red / Network (sin usar)',
    'adx_open': 'Opening',
    'adx_over': 'Game Over',
    'adx_rank': 'Ranking',
    'adx_s000': 'Escenario: Airship',
    'adx_s010': 'Escenario: Desert (?)',
    'adx_s020': 'Escenario: Factory (?)',
    'adx_s030': 'Escenario: Clock Tower (?)',
    'adx_s040': 'Escenario: River (?)',
    'adx_s050': 'Escenario: Cave',
    'adx_s060': 'Escenario: Carnival (?)',
    'adx_s070': 'Escenario: Swamp (?)',
    'adx_s080': 'Escenario: Abyss 1',
    'adx_s090': 'Escenario: Abyss 2',
    'adx_s0a0': 'Escenario: Abyss 3',
    'adx_s0b0': 'Training (?)',
    'adx_selc': 'Seleccion de personaje',
    'adx_staf': 'Staff Roll',
    'adx_wins': 'Winner',
}


# ======================================================================
# CODEC CRI ADX tipo 3 (puerto de la formula de vgmstream;
# historial de prediccion persistente entre frames, como un
# reproductor ADX real)
# ======================================================================

def parse_adx_header(data):
    if len(data) < 0x20 or data[0:2] != b'\x80\x00':
        raise ValueError('no es un stream ADX valido (falta la firma 0x80 0x00)')
    if data[4] != 3:
        raise ValueError('tipo de ADX %d no soportado (solo el tipo 3)' % data[4])
    frame_size = data[5]
    if data[6] != 4:
        raise ValueError('ADX de %d bits no soportado (solo 4 bits)' % data[6])
    channels = data[7]
    if channels not in (1, 2):
        raise ValueError('ADX con %d canales no soportado' % channels)
    sample_rate = struct.unpack('>i', data[8:12])[0]
    num_samples = struct.unpack('>i', data[0x0c:0x10])[0]
    cutoff = struct.unpack('>H', data[0x10:0x12])[0]
    version = struct.unpack('>H', data[0x12:0x14])[0]
    if version not in (0x0300, 0x0400):
        raise ValueError('version de ADX 0x%04X no soportada' % version)
    start_offset = struct.unpack('>H', data[0x02:0x04])[0] + 4
    if start_offset + frame_size > len(data):
        raise ValueError('offset de datos ADX fuera de rango')
    return dict(frame_size=frame_size, channels=channels, sample_rate=sample_rate,
                num_samples=num_samples, cutoff=cutoff, version=version,
                start_offset=start_offset, header=data[:start_offset])


def calc_coeffs(cutoff, sample_rate):
    z = math.cos(2.0 * math.pi * cutoff / sample_rate)
    a = math.sqrt(2.0) - z
    b = math.sqrt(2.0) - 1.0
    c = (a - math.sqrt((a + b) * (a - b))) / b
    return int(c * 8192), int(c * c * -4096)


def adx_decode(data, yield_cb=None, progress=None):
    """ADX -> PCM16 entrelazado. Retorna (bytes, rate, channels).
    yield_cb (opcional) se llama cada 256 frames para ceder CPU
    (lo usa la precarga en segundo plano para no trabar la GUI).
    progress(done, total) informa el avance cada 256 frames
    (lo usa la barra de carga)."""
    h = parse_adx_header(data)
    fsz, ch, sr, ns, cutoff, ver, soff = (h['frame_size'], h['channels'], h['sample_rate'],
                                          h['num_samples'], h['cutoff'], h['version'], h['start_offset'])
    coef1, coef2 = calc_coeffs(cutoff, sr)
    samples_per_frame = (fsz - 2) * 2
    total_frames = (len(data) - soff) // fsz
    out = array.array('h', [0] * (ns * ch))
    is_v3 = (ver == 0x0300)
    hist = [[0, 0] for _ in range(ch)]
    for f in range(total_frames):
        base_fr = soff + f * fsz
        fr = data[base_fr:base_fr + fsz]
        if len(fr) < fsz:
            break
        c = f % ch
        base = (f // ch) * samples_per_frame
        if base >= ns:
            continue
        lim = ns - base
        if lim > samples_per_frame:
            lim = samples_per_frame
        raws = struct.unpack('>h', fr[0:2])[0]
        scale = 0 if (fr[0] == 0x80 and fr[1] == 0x01) else raws + 1
        hh = hist[c]
        h1 = hh[0]
        h2 = hh[1]
        pos = base * ch + c
        # De a pares: cada byte trae 2 samples (mitad de iteraciones).
        nb = lim >> 1
        if is_v3:
            for j in range(nb):
                b = fr[2 + j]
                s = b >> 4
                if s & 8:
                    s -= 16
                v = s * scale + ((coef1 * h1) >> 12) + ((coef2 * h2) >> 12)
                v = min(max(v, -32768), 32767)
                out[pos] = v
                pos += ch
                h2, h1 = h1, v
                s = b & 15
                if s & 8:
                    s -= 16
                v = s * scale + ((coef1 * h1) >> 12) + ((coef2 * h2) >> 12)
                v = min(max(v, -32768), 32767)
                out[pos] = v
                pos += ch
                h2, h1 = h1, v
            if lim & 1:
                b = fr[2 + nb]
                s = b >> 4
                if s & 8:
                    s -= 16
                v = s * scale + ((coef1 * h1) >> 12) + ((coef2 * h2) >> 12)
                v = min(max(v, -32768), 32767)
                out[pos] = v
                h2, h1 = h1, v
        else:
            for j in range(nb):
                b = fr[2 + j]
                s = b >> 4
                if s & 8:
                    s -= 16
                v = s * scale + ((coef1 * h1 + coef2 * h2) >> 12)
                v = min(max(v, -32768), 32767)
                out[pos] = v
                pos += ch
                h2, h1 = h1, v
                s = b & 15
                if s & 8:
                    s -= 16
                v = s * scale + ((coef1 * h1 + coef2 * h2) >> 12)
                v = min(max(v, -32768), 32767)
                out[pos] = v
                pos += ch
                h2, h1 = h1, v
            if lim & 1:
                b = fr[2 + nb]
                s = b >> 4
                if s & 8:
                    s -= 16
                v = s * scale + ((coef1 * h1 + coef2 * h2) >> 12)
                v = min(max(v, -32768), 32767)
                out[pos] = v
                h2, h1 = h1, v
        hh[0] = h1
        hh[1] = h2
        if (f & 255) == 0:
            if yield_cb is not None:
                yield_cb()
            if progress is not None:
                progress(f, total_frames)
    if progress is not None:
        progress(total_frames, total_frames)
    return out.tobytes(), sr, ch


def adx_body_size(n_samples_per_ch, frame_size, channels):
    """Tamano exacto en bytes que va a ocupar el cuerpo ADX."""
    import math as _m
    nfr = (n_samples_per_ch + 31) // 32
    return nfr * channels * frame_size


def adx_encode(pcm16_bytes, sample_rate, channels, cutoff, version, frame_size,
               progress=None):
    """PCM16 entrelazado -> frames ADX (sin cabecera). Retorna
    (body_bytes, n_samples_por_canal). progress(done, total) se llama
    cada tanto para la barra de progreso (puede ser None)."""
    coef1, coef2 = calc_coeffs(cutoff, sample_rate)
    samples_per_frame = (frame_size - 2) * 2
    src = array.array('h')
    src.frombytes(pcm16_bytes)
    n_per_ch = len(src) // channels
    n_frames_per_ch = (n_per_ch + samples_per_frame - 1) // samples_per_frame
    total = n_frames_per_ch * channels
    done = 0
    frames_out = [None] * total
    is_v3 = (version == 0x0300)
    spack = struct.pack
    for ch in range(channels):
        h1 = h2 = 0
        for fr in range(n_frames_per_ch):
            base = fr * samples_per_frame
            block = []
            for i in range(samples_per_frame):
                idx = base + i
                block.append(src[idx * channels + ch] if idx < n_per_ch else 0)
            sh1, sh2 = h1, h2
            maxabs = 1
            for s0 in block:
                if is_v3:
                    pred = ((coef1 * sh1) >> 12) + ((coef2 * sh2) >> 12)
                else:
                    pred = (coef1 * sh1 + coef2 * sh2) >> 12
                d = s0 - pred
                if abs(d) > maxabs:
                    maxabs = abs(d)
                sh2, sh1 = sh1, s0
            scale = max(1, (maxabs + 6) // 7)
            if scale > 32767:
                scale = 32767
            nibs = bytearray(frame_size - 2)
            for i, s0 in enumerate(block):
                if is_v3:
                    pred = ((coef1 * h1) >> 12) + ((coef2 * h2) >> 12)
                else:
                    pred = (coef1 * h1 + coef2 * h2) >> 12
                d = s0 - pred
                nib = round(d / scale)
                nib = max(-8, min(7, nib))
                recon = nib * scale + pred
                recon = max(-32768, min(32767, recon))
                h2, h1 = h1, recon
                if i % 2 == 0:
                    nibs[i // 2] = (nib & 0xF) << 4
                else:
                    nibs[i // 2] |= (nib & 0xF)
            frames_out[fr * channels + ch] = spack('>h', scale - 1) + bytes(nibs)
            done += 1
            if progress is not None and (done & 63) == 0:
                progress(done, total)
    if progress is not None:
        progress(total, total)
    return b''.join(frames_out), n_per_ch


# ======================================================================
# BLOQUE DE LOOP DEL HEADER ADX
# Layout verificado contra los 23 temas del juego:
#   0x14 u16: valor X (se preserva tal cual, significado interno)
#   0x16 u16: siempre 1    0x18 u32: siempre 1
#   0x1C u32: loop_start (samples)   0x20 u32: loop_start (byte)
#   0x24 u32: loop_end (samples)     0x28 u32: loop_end (byte)
# donde byte = inicio_datos + (sample//32)*canales*tam_frame.
# ======================================================================

LOOP_OFF = 0x14
LOOP_BLOCK_LEN = 24  # 0x14..0x2B


def header_data_off(header):
    """Offset de datos leyendo solo la cabecera (sin validar el cuerpo)."""
    return struct.unpack('>H', header[0x02:0x04])[0] + 4


def header_has_loop(header):
    """True si la cabecera trae bloque de loop (vale tanto para el
    archivo completo como para la cabecera sola). El discriminante
    real es el largo del header: sin loop mide 36 bytes y con loop
    44 o mas (el string "(c)CRI" de los headers cortos cae dentro
    de la ventana 0x14-0x2B y no debe confundirse con un loop)."""
    if len(header) < 0x20:
        return False
    data_off = struct.unpack('>H', header[0x02:0x04])[0] + 4
    if data_off < LOOP_OFF + LOOP_BLOCK_LEN:
        return False
    return header[LOOP_OFF:LOOP_OFF + LOOP_BLOCK_LEN] != b'\x00' * LOOP_BLOCK_LEN


def parse_loop_block(data):
    """Si el header trae bloque de loop, retorna dict(x, start, end).
    Si no trae (header corto), retorna None."""
    if len(data) < 0x20 or data[0:2] != b'\x80\x00' or data[4] != 3:
        return None
    if not header_has_loop(data):
        return None
    x = struct.unpack('>H', data[0x14:0x16])[0]
    start = struct.unpack('>I', data[0x1C:0x20])[0]
    end = struct.unpack('>I', data[0x24:0x28])[0]
    return dict(x=x, start=start, end=end)


def _loop_byte(data_off, sample, frame_size, channels):
    return data_off + (max(0, sample) // 32) * channels * frame_size


def build_adx_music(header_template, pcm16_bytes, sample_rate, channels,
                    cutoff, version, frame_size, loop_choice='auto',
                    loop_sel=None, progress=None):
    """Reconstruye un .bin ADX completo a partir de PCM16 entrelazado.

    loop_choice: 'auto' (si el original loopeaba, loopear todo; si no,
      sin loop), 'all' (loopear todo), 'none' (sin loop).
    loop_sel: (inicio, fin) en samples, manda sobre loop_choice
      (viene del boton "Loop = seleccion" del editor).
    Retorna (bin_bytes_sin_pad, loop_desc_str).
    """
    body, n_per_ch = adx_encode(pcm16_bytes, sample_rate, channels,
                                cutoff, version, frame_size, progress)
    has_block = header_has_loop(header_template)
    if loop_sel is not None and loop_sel[1] > loop_sel[0]:
        want_loop = True
        ls = max(0, min(loop_sel[0], n_per_ch - 1))
        le = max(ls, min(loop_sel[1] - 1, n_per_ch - 1))
        desc = 'loop seleccion %d->%d' % (ls, le)
    elif loop_choice == 'all':
        want_loop = True
        ls, le = 0, max(0, n_per_ch - 1)
        desc = 'loop completo'
    elif loop_choice == 'none':
        want_loop = False
        ls = le = 0
        desc = 'sin loop'
    else:  # auto
        want_loop = has_block
        ls, le = 0, max(0, n_per_ch - 1)
        desc = 'loop completo' if want_loop else 'sin loop'

    if want_loop and has_block:
        header = bytearray(header_template)
        data_off = header_data_off(header_template)
    elif want_loop and not has_block:
        base = bytearray(header_template[:LOOP_OFF])
        x = 1
        header = base + struct.pack('>HHIII',
                                    x, 1, 1, 0, 0)
        # se completa abajo (start/end reales); string copyright y pad
        header += b'(c)CRI\x00\x00'
        data_off = len(header)
        header[0x02:0x04] = struct.pack('>H', data_off - 4)
    elif not want_loop and has_block:
        header = bytearray(header_template[:LOOP_OFF])
        header += b'\x00' * 10 + b'(c)CRI'
        header[0x02:0x04] = struct.pack('>H', 32)
        data_off = 36
    else:
        header = bytearray(header_template)
        data_off = header_data_off(header_template)

    header[8:12] = struct.pack('>i', sample_rate)
    header[0x0c:0x10] = struct.pack('>i', n_per_ch)
    header[7] = channels
    if want_loop:
        header[0x1C:0x20] = struct.pack('>I', ls)
        header[0x20:0x24] = struct.pack('>I', _loop_byte(data_off, ls, frame_size, channels))
        header[0x24:0x28] = struct.pack('>I', le)
        header[0x28:0x2C] = struct.pack('>I', _loop_byte(data_off, le, frame_size, channels))
    return bytes(header) + body, desc


def strict_body_budget(orig_total_size, header_template, loop_choice='auto',
                       loop_sel=None):
    """Maximo tamano del CUERPO ADX para que el .bin final, en modo
    estricto, pese EXACTAMENTE lo mismo que el original."""
    has_block = header_has_loop(header_template)
    if loop_sel is not None and loop_sel[1] > loop_sel[0]:
        want_loop = True
    elif loop_choice == 'all':
        want_loop = True
    elif loop_choice == 'none':
        want_loop = False
    else:
        want_loop = has_block
    if want_loop and has_block:
        hdr_len = len(header_template)
    elif want_loop and not has_block:
        hdr_len = LOOP_OFF + LOOP_BLOCK_LEN + 8  # bloque nuevo + '(c)CRI\0\0'
    elif not want_loop and has_block:
        hdr_len = 36
    else:
        hdr_len = len(header_template)
    return max(0, orig_total_size - hdr_len)


# ======================================================================
# AUDIO: wav nativo, ffmpeg para el resto, resampleo estereo correcto
# ======================================================================

def write_wav(path, pcm16_bytes, sample_rate, stereo):
    w = wave.open(path, 'wb')
    w.setnchannels(2 if stereo else 1)
    w.setsampwidth(2)
    w.setframerate(sample_rate)
    w.writeframes(pcm16_bytes)
    w.close()


def interleave_pcm16(left_bytes, right_bytes):
    l = array.array('h')
    l.frombytes(left_bytes)
    r = array.array('h')
    r.frombytes(right_bytes)
    out = array.array('h')
    for a, b in zip(l, r):
        out.append(a)
        out.append(b)
    return out.tobytes()


def read_wav_native(fpath):
    """Lee un .wav PCM (8/16/24/32 bits, mono o estereo).
    Retorna (pcm16_bytes, rate, nch)."""
    w = wave.open(fpath, 'rb')
    try:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        nf = w.getnframes()
        frames = w.readframes(nf)
    finally:
        w.close()
    if nch not in (1, 2):
        raise ValueError('el WAV tiene %d canales (solo mono o estereo)' % nch)
    if sw == 1:
        pcm16 = b''.join(struct.pack('<h', (b - 128) << 8) for b in frames)
    elif sw == 2:
        pcm16 = frames
    elif sw == 3:
        out = bytearray()
        for i in range(0, len(frames) - 2, 3):
            v = int.from_bytes(frames[i:i + 3], 'little', signed=True)
            out.extend(struct.pack('<h', v >> 8))
        pcm16 = bytes(out)
    elif sw == 4:
        out = bytearray()
        for i in range(0, len(frames) - 3, 4):
            v = struct.unpack_from('<i', frames, i)[0]
            out.extend(struct.pack('<h', max(-32768, min(32767, v >> 16))))
        pcm16 = bytes(out)
    else:
        raise ValueError('profundidad WAV no soportada (%d-bit)' % (sw * 8))
    return pcm16, sr, nch


def resample_mono_pcm16(pcm16, src_rate, dst_rate):
    if src_rate == dst_rate or not pcm16:
        return pcm16
    n = len(pcm16) // 2
    src = array.array('h')
    src.frombytes(pcm16)
    ratio = dst_rate / src_rate
    out_len = int(n * ratio)
    out = array.array('h', [0] * out_len)
    for i in range(out_len):
        s = i / ratio
        i0 = int(s)
        frac = s - i0
        if i0 + 1 < n:
            out[i] = int(src[i0] * (1 - frac) + src[i0 + 1] * frac)
        else:
            out[i] = src[-1] if n else 0
    return out.tobytes()


def resample_stereo_pcm16(pcm16, src_rate, dst_rate):
    """Resampleo por canal (no mezcla L/R como haria resamplear
    el stream entrelazado de una)."""
    if src_rate == dst_rate:
        return pcm16
    arr = array.array('h')
    arr.frombytes(pcm16)
    if len(arr) % 2:
        arr = arr[:-1]
    left = resample_mono_pcm16(arr[0::2].tobytes(), src_rate, dst_rate)
    right = resample_mono_pcm16(arr[1::2].tobytes(), src_rate, dst_rate)
    return interleave_pcm16(left, right)


def to_stereo_44100(pcm16, nch, src_rate):
    """Convierte cualquier PCM16 a estereo 44100 Hz (formato de la
    musica del juego)."""
    if nch == 1:
        arr = array.array('h')
        arr.frombytes(pcm16)
        dup = array.array('h')
        for v in arr:
            dup.append(v)
            dup.append(v)
        pcm16 = dup.tobytes()
    elif nch != 2:
        raise ValueError('canales no soportados: %d' % nch)
    if src_rate != 44100:
        pcm16 = resample_stereo_pcm16(pcm16, src_rate, 44100)
    return pcm16


def ffmpeg_convert(fpath):
    """Convierte mp3/flac/ogg/m4a/etc a PCM16 estereo 44100 via ffmpeg.
    Requiere ffmpeg.exe junto al programa o en el PATH."""
    ffmpeg_path = resource_path('ffmpeg.exe')
    if not os.path.isfile(ffmpeg_path):
        ffmpeg_path = shutil.which('ffmpeg.exe') or shutil.which('ffmpeg')
    if not ffmpeg_path:
        raise ValueError(
            'Para usar .%s necesitas ffmpeg.exe junto a este programa '
            '(o instalado en el PATH).\nPuedes descargarlo desde '
            'https://ffmpeg.org/download.html\n'
            'Los archivos .wav y .adx funcionan sin ffmpeg.'
            % os.path.splitext(fpath)[1].lower().lstrip('.'))
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
        tmp_path = tmp.name
    cmd = [ffmpeg_path, '-i', fpath, '-acodec', 'pcm_s16le',
           '-ac', '2', '-ar', '44100', '-y', tmp_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError('ffmpeg no pudo convertir el archivo:\n%s'
                             % result.stderr[-1500:])
        pcm16, sr, nch = read_wav_native(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return to_stereo_44100(pcm16, nch, sr)


def load_music_file(fpath):
    """Carga un archivo de reemplazo y lo deja en PCM16 estereo 44100.
    Retorna (pcm_bytes, descripcion). Acepta .wav (nativo), .adx
    (decodificado) y, con ffmpeg, mp3/flac/ogg/m4a/wma/aac."""
    ext = os.path.splitext(fpath)[1].lower()
    if ext == '.adx':
        data = open(fpath, 'rb').read()
        pcm, sr, ch = adx_decode(data)
        pcm = to_stereo_44100(pcm, ch, sr)
        return pcm, 'ADX %d Hz %s' % (sr, 'stereo' if ch == 2 else 'mono')
    if ext == '.wav':
        pcm, sr, nch = read_wav_native(fpath)
        info = 'WAV %d Hz %s' % (sr, 'stereo' if nch == 2 else 'mono')
        return to_stereo_44100(pcm, nch, sr), info
    if ext in ('.mp3', '.flac', '.ogg', '.oga', '.m4a', '.aac', '.wma', '.opus', '.aif', '.aiff'):
        return ffmpeg_convert(fpath), 'convertido con ffmpeg'
    raise ValueError('extension no soportada (%s). Usa .wav, .mp3, .flac, '
                     '.ogg, .m4a o .adx.' % ext)


# ======================================================================
# EDITOR DE AUDIO (recortar / fades / normalizar / zoom / loop)
# ======================================================================

class WaveEditor:
    MARKER_HIT_PX = 7

    def __init__(self, root, title, pcm16_interleaved, rate,
                 max_bytes=None, original_bytes=None):
        self.root = root
        self.rate = max(1, rate)
        self.stereo = True
        self.channels = 2
        self.fmt = 'adx'
        self.max_bytes = max_bytes
        self.original_bytes = original_bytes or max_bytes
        self.result = None
        self.loop_sel = None

        arr = array.array('h')
        arr.frombytes(pcm16_interleaved)
        if len(arr) % 2:
            arr = arr[:-1]
        self.left = array.array('h', arr[0::2])
        self.right = array.array('h', arr[1::2])

        self.undo_stack = []
        self.sel_start = 0
        self.sel_end = len(self.left)
        self.zoom = 1.0
        self.scroll = 0.0
        self.drag_mode = None
        self.playing = False
        self.play_after_id = None
        # Picos precalculados para dibujar la onda (ver _rebuild_peaks).
        self._peaks = []
        self._peak_win = 1
        self._peaks_dirty = True

        self.win = tk.Toplevel(root)
        self.win.title(title)
        self.win.geometry('820x490')
        self.win.minsize(680, 430)
        self.win.transient(root)
        self.win.grab_set()
        self.win.protocol('WM_DELETE_WINDOW', self._on_cancel)
        self._build_ui()
        self.win.bind('<space>', lambda e: self._toggle_play())
        self.win.bind('<Control-z>', lambda e: self._undo())
        self.win.bind('<plus>', lambda e: self._zoom(2))
        self.win.bind('<minus>', lambda e: self._zoom(0.5))
        self.win.after(80, self._redraw)

    def n_frames(self):
        return len(self.left)

    def _push_undo(self):
        right_copy = array.array('h', self.right) if self.right is not None else None
        self.undo_stack.append((array.array('h', self.left), right_copy))
        if len(self.undo_stack) > 20:
            self.undo_stack.pop(0)
        self.loop_sel = None
        self._peaks_dirty = True  # el audio va a cambiar: picos a reconstruir

    def _undo(self):
        if not self.undo_stack:
            return
        left, right = self.undo_stack.pop()
        self.left, self.right = left, right
        self.sel_start = 0
        self.sel_end = self.n_frames()
        self._peaks_dirty = True
        self._redraw()
        self._update_info()

    def _rebuild_peaks(self):
        """Precalcula min/max por ventana del canal izquierdo para que
        _redraw dibuje en O(ancho) en vez de O(samples). Se reconstruye
        solo cuando el audio cambia (flag _peaks_dirty), no en cada
        movimiento del mouse. min/max sobre slices van en C."""
        n = self.n_frames()
        if n == 0:
            self._peaks = []
            self._peak_win = 1
            return
        w = max(1, n // 16000)
        self._peak_win = w
        L = self.left
        peaks = []
        for i in range(0, n, w):
            seg = L[i:i + w]
            peaks.append((min(seg), max(seg)))
        self._peaks = peaks

    def _estimate_bytes(self):
        """Tamano del CUERPO ADX que resultaria (bytes exactos)."""
        n = self.n_frames()
        nfr = (n + 31) // 32
        return nfr * self.channels * 18

    def _max_frames_allowed(self):
        if self.max_bytes is None:
            return None
        return self.max_bytes // (18 * self.channels) * 32

    def _build_ui(self):
        info = tk.Frame(self.win)
        info.pack(fill='x', padx=10, pady=(8, 2))
        self.info_label = tk.Label(info, font=('Consolas', 9), fg='#333', justify='left')
        self.info_label.pack(side='left')
        self.limit_label = tk.Label(info, font=('Consolas', 9, 'bold'), fg='red')
        self.limit_label.pack(side='right')

        self.canvas = tk.Canvas(self.win, bg='#1a1a2e', height=180, highlightthickness=0,
                                cursor='sb_h_double_arrow')
        self.canvas.pack(fill='both', expand=True, padx=10, pady=4)
        self.canvas.bind('<Configure>', lambda e: self._redraw())
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<MouseWheel>', self._on_wheel)
        self.canvas.bind('<Button-4>', lambda e: self._zoom(1.4))
        self.canvas.bind('<Button-5>', lambda e: self._zoom(1 / 1.4))

        scrollf = tk.Frame(self.win)
        scrollf.pack(fill='x', padx=10)
        self.scroll_var = tk.DoubleVar(value=0.0)
        self.scroll_scale = tk.Scale(scrollf, from_=0, to=1, resolution=0.001, orient='horizontal',
                                     variable=self.scroll_var, showvalue=0,
                                     command=lambda v: self._on_scroll())
        self.scroll_scale.pack(fill='x')

        ctrl = tk.Frame(self.win)
        ctrl.pack(fill='x', padx=10, pady=4)
        self.btn_play = tk.Button(ctrl, text='Play (espacio)', width=13, command=self._toggle_play)
        self.btn_play.pack(side='left')
        tk.Button(ctrl, text='Zoom +', width=7, command=lambda: self._zoom(2)).pack(side='left', padx=(8, 0))
        tk.Button(ctrl, text='Zoom -', width=7, command=lambda: self._zoom(0.5)).pack(side='left', padx=2)
        tk.Button(ctrl, text='Ver todo', width=8, command=self._zoom_reset).pack(side='left', padx=(0, 8))
        tk.Button(ctrl, text='Deshacer (Ctrl+Z)', width=16, command=self._undo).pack(side='left')

        edit = tk.Frame(self.win)
        edit.pack(fill='x', padx=10, pady=(0, 4))
        tk.Button(edit, text='Recortar a seleccion', command=self._cmd_trim_to_selection).pack(side='left')
        tk.Button(edit, text='Eliminar seleccion', command=self._cmd_delete_selection).pack(side='left', padx=4)
        tk.Button(edit, text='Fade In', command=self._cmd_fade_in).pack(side='left', padx=4)
        tk.Button(edit, text='Fade Out', command=self._cmd_fade_out).pack(side='left', padx=4)
        tk.Button(edit, text='Normalizar', command=self._cmd_normalize).pack(side='left', padx=4)
        self.btn_loop = tk.Button(edit, text='Loop = seleccion', command=self._cmd_loop_sel,
                                  bg='#e8e8e8')
        self.btn_loop.pack(side='left', padx=4)

        markers = tk.Frame(self.win)
        markers.pack(fill='x', padx=10, pady=(0, 4))
        tk.Label(markers, text='Sel. inicio (ms):', font=('Segoe UI', 9)).pack(side='left')
        self.ini_var = tk.StringVar(value='0')
        tk.Entry(markers, textvariable=self.ini_var, width=8).pack(side='left', padx=(2, 10))
        tk.Label(markers, text='Sel. fin (ms):', font=('Segoe UI', 9)).pack(side='left')
        self.fin_var = tk.StringVar(value='0')
        tk.Entry(markers, textvariable=self.fin_var, width=8).pack(side='left', padx=2)
        tk.Button(markers, text='Ir', width=4, command=self._set_selection_from_fields).pack(side='left', padx=6)

        btns = tk.Frame(self.win)
        btns.pack(fill='x', padx=10, pady=(4, 10))
        tk.Button(btns, text='Cancelar', width=10, command=self._on_cancel).pack(side='right', padx=4)
        tk.Button(btns, text='Aplicar cambios', width=16, command=self._on_apply,
                  bg='#4a9eff', fg='white').pack(side='right')

        self._update_info()

    def _view_range(self):
        n = self.n_frames()
        view = max(1, int(n / self.zoom))
        start = max(0, min(n, int(self.scroll * max(0, n - view))))
        end = min(n, start + view)
        return start, end

    def _frame_to_x(self, frame, start, end, w):
        span = max(1, end - start)
        return int((frame - start) * w / span)

    def _x_to_frame(self, x, start, end, w):
        span = max(1, end - start)
        return int(start + x * span / max(1, w))

    def _redraw(self):
        c = self.canvas
        c.delete('all')
        w = c.winfo_width() or 780
        h = c.winfo_height() or 180
        mid = h // 2
        n = self.n_frames()
        if n == 0:
            return
        if self._peaks_dirty:
            self._rebuild_peaks()
            self._peaks_dirty = False
        start, end = self._view_range()
        span_view = end - start
        c.create_line(0, mid, w, mid, fill='#333')
        if self._peaks and span_view > w * 2:
            # Vista amplia: agregar picos precalculados (rapido).
            pw = self._peak_win
            pk = self._peaks
            npk = len(pk)
            for x in range(w):
                f0 = start + x * span_view // w
                f1 = start + (x + 1) * span_view // w
                i0 = f0 // pw
                i1 = (f1 - 1) // pw if f1 > f0 else i0
                if i0 >= npk:
                    break
                if i1 >= npk:
                    i1 = npk - 1
                lo = 32767
                hi = -32768
                for pi in range(i0, i1 + 1):
                    p0, p1 = pk[pi]
                    if p0 < lo:
                        lo = p0
                    if p1 > hi:
                        hi = p1
                amp = -lo if lo < 0 and -lo > hi else hi
                hgt = min(mid - 4, amp * (mid - 4) // 32768)
                c.create_line(x, mid - hgt, x, mid + hgt, fill='#4a9eff')
        else:
            # Con zoom (pocos samples en vista): dibujo exacto, barato.
            step = max(1, span_view // max(1, w))
            left = self.left
            x = 0
            i = start
            while i < end and x < w:
                chunk_max = 0
                j = min(end, i + step)
                for k in range(i, j):
                    v = abs(left[k])
                    if v > chunk_max:
                        chunk_max = v
                hgt = min(mid - 4, chunk_max * (mid - 4) // 32768)
                c.create_line(x, mid - hgt, x, mid + hgt, fill='#4a9eff')
                x += 1
                i += step
        s0, s1 = self.sel_start, self.sel_end
        if s1 > s0:
            x0 = self._frame_to_x(max(s0, start), start, end, w)
            x1 = self._frame_to_x(min(s1, end), start, end, w)
            c.create_rectangle(x0, 0, x1, h, fill='#2255aa', outline='', stipple='gray25')
        if start <= s0 <= end:
            xa = self._frame_to_x(s0, start, end, w)
            c.create_line(xa, 0, xa, h, fill='#00ff00', width=2)
        if start <= s1 <= end:
            xb = self._frame_to_x(s1, start, end, w)
            c.create_line(xb, 0, xb, h, fill='#ff4444', width=2)
        if self.max_bytes is not None:
            max_frames = self._max_frames_allowed()
            if max_frames is not None and start <= max_frames <= end:
                xl = self._frame_to_x(max_frames, start, end, w)
                c.create_line(xl, 0, xl, h, fill='red', width=2, dash=(3, 2))
        sel_ms = int((s1 - s0) / self.rate * 1000) if s1 > s0 else 0
        c.create_text(w // 2, 10, text='Seleccion: %d ms  |  zoom x%.1f' % (sel_ms, self.zoom),
                      fill='#0f0', font=('Consolas', 9))
        self._update_info()

    def _update_info(self):
        n = self.n_frames()
        dur_ms = int(n / self.rate * 1000)
        est = self._estimate_bytes()
        loop_txt = ''
        if self.loop_sel is not None:
            loop_txt = ' | LOOP=%d->%d' % self.loop_sel
        self.info_label.configure(
            text='ADX estereo 44100 Hz | Duracion: %d ms | Cuerpo ADX: %d B%s'
            % (dur_ms, est, loop_txt))
        if self.max_bytes is not None:
            over = est > self.max_bytes
            self.limit_label.configure(
                text=('SUPERA EL LIMITE (%d / %d B)' if over else 'dentro del limite (%d / %d B)')
                % (est, self.max_bytes), fg='red' if over else '#1a5c1a')
        else:
            self.limit_label.configure(text='')

    def _on_press(self, event):
        w = self.canvas.winfo_width() or 780
        start, end = self._view_range()
        frame = self._x_to_frame(event.x, start, end, w)
        x0 = self._frame_to_x(self.sel_start, start, end, w)
        x1 = self._frame_to_x(self.sel_end, start, end, w)
        if self.sel_end > self.sel_start and abs(event.x - x0) <= self.MARKER_HIT_PX:
            self.drag_mode = 'start'
        elif self.sel_end > self.sel_start and abs(event.x - x1) <= self.MARKER_HIT_PX:
            self.drag_mode = 'end'
        else:
            self.drag_mode = 'new'
            self.sel_start = self.sel_end = max(0, min(self.n_frames(), frame))
        self._redraw()

    def _on_drag(self, event):
        w = self.canvas.winfo_width() or 780
        start, end = self._view_range()
        frame = max(0, min(self.n_frames(), self._x_to_frame(event.x, start, end, w)))
        if self.drag_mode == 'start':
            self.sel_start = min(frame, self.sel_end)
        elif self.drag_mode == 'end':
            self.sel_end = max(frame, self.sel_start)
        elif self.drag_mode == 'new':
            if frame >= self.sel_start:
                self.sel_end = frame
            else:
                self.sel_end = self.sel_start
                self.sel_start = frame
        self._redraw()

    def _on_release(self, event):
        self.drag_mode = None
        self.ini_var.set('%.0f' % (self.sel_start / self.rate * 1000))
        self.fin_var.set('%.0f' % (self.sel_end / self.rate * 1000))

    def _on_wheel(self, event):
        if event.delta > 0:
            self._zoom(1.4)
        else:
            self._zoom(1 / 1.4)

    def _on_scroll(self):
        self.scroll = self.scroll_var.get()
        self._redraw()

    def _zoom(self, factor):
        self.zoom = max(1.0, min(200.0, self.zoom * factor))
        self._redraw()

    def _zoom_reset(self):
        self.zoom = 1.0
        self.scroll = 0.0
        self.scroll_var.set(0.0)
        self._redraw()

    def _set_selection_from_fields(self):
        try:
            ini = max(0, int(float(self.ini_var.get()) / 1000 * self.rate))
            fin = min(self.n_frames(), int(float(self.fin_var.get()) / 1000 * self.rate))
            if fin > ini:
                self.sel_start, self.sel_end = ini, fin
                self._redraw()
        except ValueError:
            pass

    def _cmd_trim_to_selection(self):
        if self.sel_end <= self.sel_start:
            messagebox.showinfo('Editor', 'Primero arrastra sobre la onda para elegir una seleccion.')
            return
        self._push_undo()
        s0, s1 = self.sel_start, self.sel_end
        self.left = self.left[s0:s1]
        self.right = self.right[s0:s1]
        self.sel_start, self.sel_end = 0, self.n_frames()
        self._zoom_reset()
        self._redraw()

    def _cmd_delete_selection(self):
        if self.sel_end <= self.sel_start:
            messagebox.showinfo('Editor', 'Primero elegi una seleccion para eliminar.')
            return
        self._push_undo()
        s0, s1 = self.sel_start, self.sel_end
        self.left = self.left[:s0] + self.left[s1:]
        self.right = self.right[:s0] + self.right[s1:]
        self.sel_start = self.sel_end = s0
        self._redraw()

    def _fade_range(self):
        if self.sel_end > self.sel_start:
            return self.sel_start, self.sel_end
        return 0, self.n_frames()

    def _cmd_fade_in(self):
        s0, s1 = self._fade_range()
        if s1 <= s0:
            return
        self._push_undo()
        L = self.left
        R = self.right
        n = s1 - s0
        for i in range(n):
            g = i / n
            j = s0 + i
            L[j] = int(L[j] * g)
            R[j] = int(R[j] * g)
        self._redraw()

    def _cmd_fade_out(self):
        s0, s1 = self._fade_range()
        if s1 <= s0:
            return
        self._push_undo()
        L = self.left
        R = self.right
        n = s1 - s0
        for i in range(n):
            g = 1 - (i / n)
            j = s0 + i
            L[j] = int(L[j] * g)
            R[j] = int(R[j] * g)
        self._redraw()

    def _cmd_normalize(self):
        if self.n_frames() == 0:
            return
        peak = max((abs(v) for v in self.left), default=0)
        peak = max(peak, max((abs(v) for v in self.right), default=0))
        if peak == 0:
            messagebox.showinfo('Editor', 'El audio esta en silencio, no hay nada que normalizar.')
            return
        target = 32000
        gain = target / peak
        if gain <= 1.0001:
            messagebox.showinfo('Editor', 'El audio ya esta cerca del maximo, no hace falta normalizar.')
            return
        self._push_undo()
        self.left = array.array('h', (max(-32768, min(32767, int(v * gain))) for v in self.left))
        self.right = array.array('h', (max(-32768, min(32767, int(v * gain))) for v in self.right))
        self._redraw()

    def _cmd_loop_sel(self):
        if self.sel_end <= self.sel_start:
            messagebox.showinfo('Editor', 'Primero elegi una seleccion para usarla como loop.')
            return
        self.loop_sel = (self.sel_start, self.sel_end)
        self.btn_loop.configure(bg='#9fd08a')
        self._update_info()

    def _toggle_play(self):
        if self.playing:
            self._stop_play()
            return
        if not winsound:
            messagebox.showinfo('Editor', 'winsound no disponible en este Python.')
            return
        s0, s1 = (self.sel_start, self.sel_end) if self.sel_end > self.sel_start else (0, self.n_frames())
        if s1 <= s0:
            return
        seg = interleave_pcm16(self.left[s0:s1].tobytes(), self.right[s0:s1].tobytes())
        tmp = os.path.join(tempfile.gettempdir(), 'mvc2_music_preview.wav')
        write_wav(tmp, seg, self.rate, True)
        winsound.PlaySound(None, winsound.SND_PURGE)
        winsound.PlaySound(tmp, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        self.playing = True
        self.btn_play.configure(text='Stop (espacio)')
        dur_ms = max(50, int((s1 - s0) / self.rate * 1000))
        self.play_after_id = self.win.after(dur_ms, self._on_play_finished)

    def _on_play_finished(self):
        self.playing = False
        self.play_after_id = None
        try:
            self.btn_play.configure(text='Play (espacio)')
        except tk.TclError:
            pass

    def _stop_play(self):
        if winsound:
            winsound.PlaySound(None, winsound.SND_PURGE)
        self.playing = False
        if self.play_after_id:
            self.win.after_cancel(self.play_after_id)
            self.play_after_id = None
        try:
            self.btn_play.configure(text='Play (espacio)')
        except tk.TclError:
            pass

    def _on_apply(self):
        pcm = interleave_pcm16(self.left.tobytes(), self.right.tobytes())
        if self.max_bytes is not None:
            est = self._estimate_bytes()
            if est > self.max_bytes:
                messagebox.showwarning('Editor',
                    'El audio editado (cuerpo ADX %d B) sigue superando el limite '
                    'estricto (%d B).\nRecorta o elimina mas antes de aplicar.'
                    % (est, self.max_bytes))
                return
        self._stop_play()
        self.result = (pcm, self.loop_sel)
        self.win.destroy()

    def _on_cancel(self):
        self._stop_play()
        self.result = None
        self.win.destroy()

    def run(self):
        self.win.wait_window()
        return self.result


# ======================================================================
# APP PRINCIPAL
# ======================================================================

PREVIEW_WAV = os.path.join(tempfile.gettempdir(), 'mvc2_music_preview.wav')


class MusicApp:
    def __init__(self, root):
        self.root = root
        self.log_path = os.path.join(app_dir(), 'MVC2_MusicTool.log')
        self.session = None
        self.mode = tk.StringVar(value='estricto')
        self.loop_choice = tk.StringVar(value='auto')
        self.hybrid_flags = {}
        self.indicadores = {}
        self.gif_frames = []
        self.gif_label = None
        self.gif_index = 0
        self._warm_token = 0  # (sin uso, reservado)
        self._last_play = None  # (idx, pcm_bytes, wav_path) ya preparado
        self._play_token = 0  # cada carga nueva abandona a la anterior
        self._encode_state = None  # dialogo de conversion/codificacion activo
        self._encode_token = 0  # cada encode nuevo (o Cancelar) abandona al anterior
        self._encode_progress = None  # (done, total) lo escribe el worker
        self._encode_outcome = None  # (kind, token, payload) resultado listo
        self._loader = None  # dict(token, label) mientras algo se prepara
        self._load_progress = None  # (done, total) lo escribe el worker
        self._load_outcome = None  # (kind, token, payload) resultado listo
        root.title('MVC2 Music Tool')
        root.geometry('900x600')
        root.minsize(760, 500)
        try:
            root.iconbitmap(resource_path('app.ico'))
        except Exception:
            pass
        self._load_indicadores()

        top = tk.Frame(root)
        top.pack(fill='x', padx=8, pady=(8, 0))
        tk.Label(top, text='MVC2 MUSIC TOOL - reemplazo de canciones',
                 font=('Consolas', 12, 'bold'), fg='#0a1a4f').pack()
        self.info_label = tk.Label(root, text='Arrastra la carpeta gdrom o un adx_*.bin',
                                   font=('Segoe UI', 9), fg='#555')
        self.info_label.pack(fill='x', padx=8)

        modo_frame = tk.Frame(root)
        modo_frame.pack(fill='x', padx=8, pady=(4, 0))
        tk.Label(modo_frame, text='Modo:', font=('Segoe UI', 9, 'bold')).pack(side='left')
        for val, txt, clr in (('estricto', 'ESTRICTO', '#8b0000'),
                              ('hibrido', 'HIBRIDO', '#7a5c00'),
                              ('libre', 'LIBRE', '#1a5c1a')):
            tk.Radiobutton(modo_frame, text=txt, variable=self.mode, value=val,
                           font=('Segoe UI', 8, 'bold'), fg=clr, selectcolor='#fff',
                           indicatoron=0, width=9, relief='raised', bd=1,
                           command=self._on_mode_change).pack(side='left', padx=2)
        tk.Label(modo_frame, text='Loop al reemplazar:', font=('Segoe UI', 9)).pack(side='left', padx=(14, 2))
        self.loop_combo = ttk.Combobox(modo_frame, textvariable=self.loop_choice, width=22,
                                       state='readonly', values=('auto', 'all', 'none'))
        self.loop_combo.pack(side='left')
        self.loop_combo.set('auto')
        tk.Label(modo_frame, text='auto=como el original',
                 font=('Segoe UI', 8), fg='#555').pack(side='left', padx=4)

        btns = tk.Frame(root)
        btns.pack(fill='x', padx=8, pady=4)
        self.buttons = {}
        self.buttons['open'] = tk.Button(btns, text='Cargar carpeta...', command=self.cmd_open_folder)
        self.buttons['open'].pack(side='left')
        self.buttons['openbin'] = tk.Button(btns, text='Cargar .bin...', command=self.cmd_open_bin)
        self.buttons['openbin'].pack(side='left', padx=4)
        self.buttons['extract'] = tk.Button(btns, text='Extraer WAV...', command=self.cmd_extract,
                                            state='disabled')
        self.buttons['extract'].pack(side='left', padx=4)
        self.buttons['play'] = tk.Button(btns, text='Reproducir', command=self.cmd_play, state='disabled')
        self.buttons['play'].pack(side='left', padx=4)
        self.buttons['stop'] = tk.Button(btns, text='Detener', command=self.cmd_stop, state='disabled')
        self.buttons['stop'].pack(side='left')
        self.buttons['replace'] = tk.Button(btns, text='Cargar reemplazo...', command=self.cmd_replace,
                                            state='disabled')
        self.buttons['replace'].pack(side='left', padx=4)
        self.buttons['trim'] = tk.Button(btns, text='Recortar...', command=self.cmd_trim,
                                         state='disabled')
        self.buttons['trim'].pack(side='left')
        self.buttons['unreplace'] = tk.Button(btns, text='Quitar reemplazo', command=self.cmd_unreplace,
                                              state='disabled')
        self.buttons['unreplace'].pack(side='left', padx=4)
        self.buttons['save'] = tk.Button(btns, text='Guardar todo...', command=self.cmd_save_all,
                                         state='disabled')
        self.buttons['save'].pack(side='right')
        tk.Button(btns, text='Salir', command=root.destroy).pack(side='right', padx=4)

        loadf = tk.Frame(root)
        loadf.pack(fill='x', padx=8, pady=(0, 2))
        self.load_label = tk.Label(loadf, text='', font=('Segoe UI', 8), fg='#555')
        self.load_label.pack(side='left')
        self.load_canvas = tk.Canvas(loadf, height=14, bg='#2a2a2a', highlightthickness=0)
        self.load_canvas.pack(side='left', fill='x', expand=True, padx=(8, 0))

        mid = tk.Frame(root)
        mid.pack(fill='both', expand=True, padx=8)
        cols = ('idx', 'file', 'name', 'size', 'dur', 'loop', 'state')
        self.tree = ttk.Treeview(mid, columns=cols, show='tree headings', height=12)
        for c, w, t in (('idx', 40, 'N.'), ('file', 130, 'Archivo'), ('name', 220, 'Tema'),
                        ('size', 90, 'Tamano'), ('dur', 70, 'Duracion'),
                        ('loop', 70, 'Loop'), ('state', 100, 'Estado')):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=('w' if c in ('file', 'name', 'state') else 'e'))
        self.tree.column('#0', width=36, stretch=False, anchor='center')
        self.tree.tag_configure('repl_row', foreground='#0a8a0a')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<Double-1>', lambda e: self.cmd_play())
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        self.tree.bind('<Button-3>', self._on_right_click)
        self.tree.bind('<Button-1>', self._toggle_hybrid)

        bottom = tk.Frame(root)
        bottom.pack(fill='x', padx=8, pady=(4, 8))
        self.logbox = scrolledtext.ScrolledText(bottom, state='disabled', wrap='word',
                                                font=('Consolas', 8), height=5)
        self.logbox.pack(side='left', fill='both', expand=True, padx=(0, 8))
        self.gif_label = tk.Label(bottom)
        self.gif_label.pack(side='right', anchor='se')
        self._load_gif(resource_path('assets', 'lain.gif'))

        if HAVE_DND:
            for w in (root, self.tree, self.logbox):
                w.drop_target_register(DND_FILES)
                w.dnd_bind('<<Drop>>', self.on_drop)
            self.log('Arrastra y suelta activado: suelta la carpeta gdrom o un adx_*.bin.')
        else:
            self.log('Arrastra y suelta no disponible (falta tkinterdnd2): usa los botones.')
        self.ctx_menu = tk.Menu(root, tearoff=0)
        self.ctx_menu.add_command(label='Reproducir', command=self.cmd_play)
        self.ctx_menu.add_command(label='Cargar reemplazo...', command=self.cmd_replace)
        self.ctx_menu.add_command(label='Recortar...', command=self.cmd_trim)
        self.ctx_menu.add_command(label='Quitar reemplazo', command=self.cmd_unreplace)
        self.log('Modo inicial: ESTRICTO (los .bin nuevos pesan exactamente lo mismo).')

    # ---------- utilidades ----------

    def log(self, msg):
        self.logbox.configure(state='normal')
        self.logbox.insert('end', msg + '\n')
        self.logbox.see('end')
        self.logbox.configure(state='disabled')
        self.logbox.update_idletasks()
        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(msg + '\n')
        except Exception:
            pass

    def _load_indicadores(self):
        try:
            from PIL import Image, ImageTk
            base = resource_path('assets')
            im_v = Image.open(os.path.join(base, 'verde.png')).convert('RGBA').resize((18, 18))
            im_r = Image.open(os.path.join(base, 'rojo.png')).convert('RGBA').resize((18, 18))
            self.indicadores = {False: ImageTk.PhotoImage(im_v), True: ImageTk.PhotoImage(im_r)}
        except Exception:
            self.indicadores = {}

    def _on_mode_change(self):
        self.log('Modo: %s' % self.mode.get().upper())
        self._refresh_tree_display()

    def _load_gif(self, path):
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            w, h = img.size
            nw, nh = max(1, w // 4), max(1, h // 4)
            frames = []
            try:
                while True:
                    frame = img.copy().resize((nw, nh), Image.LANCZOS)
                    frames.append(ImageTk.PhotoImage(frame))
                    img.seek(img.tell() + 1)
            except EOFError:
                pass
            if frames:
                self.gif_frames = frames
                self._animate_gif()
        except Exception:
            pass

    def _animate_gif(self):
        if not self.gif_frames or not self.gif_label:
            return
        try:
            self.gif_label.configure(image=self.gif_frames[self.gif_index])
        except tk.TclError:
            return
        self.gif_index = (self.gif_index + 1) % len(self.gif_frames)
        self.root.after(80, self._animate_gif)

    def _refresh_tree_display(self):
        if self.session is None:
            return
        for i in range(len(self.session['tracks'])):
            iid = str(i)
            if self.mode.get() == 'hibrido' and self.indicadores:
                flag = self.hybrid_flags.get(i, False)
                self.tree.item(iid, image=self.indicadores[flag])
            else:
                self.tree.item(iid, image='')

    def _toggle_hybrid(self, event):
        if self.session is None or self.mode.get() != 'hibrido':
            return
        if self.tree.identify_region(event.x, event.y) not in ('tree', 'cell'):
            return
        if self.tree.identify_column(event.x, event.y) != '#0':
            return
        iid = self.tree.identify_row(event.y)
        if iid in (None, ''):
            return
        idx = int(iid)
        self.hybrid_flags[idx] = not self.hybrid_flags.get(idx, False)
        self._refresh_tree_display()
        self.log('  %s: %s' % (self.session['tracks'][idx]['fname'],
                               'ESTRICTO' if self.hybrid_flags[idx] else 'LIBRE'))

    def _is_strict(self, idx):
        m = self.mode.get()
        return m == 'estricto' or (m == 'hibrido' and self.hybrid_flags.get(idx, False))

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid not in (None, ''):
            self.tree.selection_set(iid)
            self.ctx_menu.post(event.x_root, event.y_root)

    def on_select(self, event=None):
        has = self.session is not None and len(self.tree.selection()) > 0
        for k in ('extract', 'play', 'stop', 'replace', 'trim', 'unreplace', 'save'):
            self.buttons[k].configure(state=('normal' if has else 'disabled'))

    def _sel_idx(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    # ---------- carga ----------

    def on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        for p in paths:
            self.handle_path(p)

    def handle_path(self, path):
        try:
            if os.path.isdir(path):
                self.load_folder(path)
            elif os.path.isfile(path):
                self.load_single_bin(path)
            else:
                self.log('Ruta no reconocida: %s' % path)
        except Exception as e:
            self.log('ERROR: %s' % e)
            messagebox.showerror('Error', str(e))

    def cmd_open_folder(self):
        path = filedialog.askdirectory(title='Elegir carpeta con los adx_*.bin (gdrom)')
        if path:
            self.handle_path(path)

    def cmd_open_bin(self):
        path = filedialog.askopenfilename(title='Elegir .bin de musica',
                                          filetypes=[('BIN', '*.bin'), ('Todos', '*.*')])
        if path:
            self.handle_path(path)

    @staticmethod
    def _try_adx(path):
        try:
            data = open(path, 'rb').read()
            h = parse_adx_header(data)
            return data, h
        except Exception:
            return None, None

    def _make_track(self, fname, path, data, h):
        base = os.path.splitext(fname)[0]
        loop = parse_loop_block(data)
        dur = h['num_samples'] / h['sample_rate'] if h['sample_rate'] else 0
        return dict(fname=fname, path=path, data=data, header=h['header'],
                    cutoff=h['cutoff'], version=h['version'], frame_size=h['frame_size'],
                    rate=h['sample_rate'], ch=h['channels'], total=len(data),
                    dur=dur, loop=loop, name=TRACK_NAMES.get(base, base),
                    pcm=None, repl=None)

    def load_folder(self, folder):
        bins = sorted(f for f in os.listdir(folder)
                      if f.lower().endswith('.bin'))
        tracks = []
        for f in bins:
            data, h = self._try_adx(os.path.join(folder, f))
            if data is not None:
                tracks.append(self._make_track(f, os.path.join(folder, f), data, h))
        if not tracks:
            messagebox.showinfo('Sin musica', 'No encontre ningun .bin ADX valido en esa carpeta.')
            return
        self.session = dict(kind='folder', dir=folder, tracks=tracks)
        self.hybrid_flags = {}
        self._last_play = None
        self._fill_tree()
        self.info_label.configure(text='%d temas desde %s' % (len(tracks), folder))
        self.log('Carpeta cargada: %s (%d temas ADX)' % (folder, len(tracks)))
        self.on_select()
        self._start_prep()

    def _start_prep(self):
        """Prepara TODOS los temas al cargar (decode + cache en disco),
        con barra de progreso. Despues todo es instantaneo."""
        self._play_token += 1
        token = self._play_token
        session = self.session
        n = len(session['tracks'])
        self._loader_start(token, 'Preparando %d temas...' % n, kind='prep')
        t = threading.Thread(target=self._prep_worker, args=(session, token),
                             daemon=True)
        t.start()

    def _prep_worker(self, session, token):
        bad = []
        try:
            tracks = session['tracks']
            n = len(tracks)
            for i, tr in enumerate(tracks):
                if token != self._play_token:
                    return
                if tr['repl'] is not None or tr['pcm'] is not None:
                    continue
                try:
                    def _pg(d, t, _i=i, _n=n, _tok=token):
                        if _tok == self._play_token:
                            self._load_progress = (
                                (_i * 1000 + d * 1000 // max(1, t)) // _n, 1000)
                    self._load_pcm_sync(tr, progress=_pg)
                except Exception:
                    bad.append(tr['fname'])
            self._load_outcome = ('prep_done', token, (session, bad))
        except Exception as e:
            self._load_outcome = ('prep_err', token, str(e))

    def load_single_bin(self, path):
        data, h = self._try_adx(path)
        if data is None:
            messagebox.showerror('No es musica',
                                 'Este archivo no parece un stream ADX (tipo 3) valido.')
            return
        fname = os.path.basename(path)
        self.session = dict(kind='single', dir=os.path.dirname(path),
                            tracks=[self._make_track(fname, path, data, h)])
        self.hybrid_flags = {}
        self._last_play = None
        self._fill_tree()
        self.info_label.configure(text='%s' % path)
        self.log('Tema cargado: %s' % path)
        self.on_select()
        self._start_prep()

    def _fill_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, tr in enumerate(self.session['tracks']):
            loop_txt = 'Si' if tr['loop'] else 'No'
            state = 'reemplazado' if tr['repl'] else 'original'
            self.tree.insert('', 'end', iid=str(idx),
                             values=(idx + 1, tr['fname'], tr['name'],
                                     '%d B' % tr['total'], '%.1f s' % tr['dur'],
                                     loop_txt, state),
                             tags=('repl_row' if tr['repl'] else ()))
        self._refresh_tree_display()

    def _set_row_state(self, idx):
        tr = self.session['tracks'][idx]
        state = 'reemplazado' if tr['repl'] else 'original'
        self.tree.item(str(idx), values=(idx + 1, tr['fname'], tr['name'],
                                         '%d B' % tr['total'], '%.1f s' % tr['dur'],
                                         'Si' if tr['loop'] else 'No', state),
                       tags=('repl_row' if tr['repl'] else ()))

    # ---------- cache en disco de temas decodificados ----------

    def _cache_dir(self):
        d = os.path.join(app_dir(), 'cache_wav')
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            return None

    def _cache_key(self, tr):
        """Nombre unico por archivo+tamano+fecha (si el .bin cambia,
        la entrada vieja deja de usarse)."""
        try:
            st = os.stat(tr['path'])
            base = os.path.splitext(tr['fname'])[0]
            return '%s_%d_%d.wav' % (base, st.st_size, int(st.st_mtime))
        except Exception:
            return None

    def _cache_wav_path(self, tr):
        d = self._cache_dir()
        k = self._cache_key(tr) if d else None
        if not k:
            return None
        p = os.path.join(d, k)
        return p if os.path.isfile(p) else None

    def _cache_get_pcm(self, tr):
        p = self._cache_wav_path(tr)
        if not p:
            return None
        try:
            pcm, sr, nch = read_wav_native(p)
            if sr == 44100 and nch == 2:
                return pcm
        except Exception:
            pass
        return None

    def _cache_put(self, tr, pcm):
        try:
            d = self._cache_dir()
            k = self._cache_key(tr) if d else None
            if not k:
                return
            write_wav(os.path.join(d, k), pcm, 44100, True)
            self._trim_cache()
        except Exception:
            pass

    def _trim_cache(self, limit_mb=256):
        """Borra los WAV mas viejos si el cache supera el limite."""
        try:
            d = os.path.join(app_dir(), 'cache_wav')
            files = []
            total = 0
            for f in os.listdir(d):
                if not f.lower().endswith('.wav'):
                    continue
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except Exception:
                    continue
                files.append((st.st_mtime, p, st.st_size))
                total += st.st_size
            if total <= limit_mb * 1024 * 1024:
                return
            files.sort()
            for _, p, sz in files:
                try:
                    os.remove(p)
                except Exception:
                    pass
                total -= sz
                if total <= limit_mb * 1024 * 1024:
                    break
        except Exception:
            pass

    def _load_pcm_sync(self, tr, progress=None):
        """PCM del tema (reemplazo, memoria, cache en disco o decode).
        Llamar desde un worker: puede tardar segundos."""
        if tr['repl'] is not None:
            return tr['repl']['pcm']
        if tr['pcm'] is not None:
            return tr['pcm']
        pcm = self._cache_get_pcm(tr)
        if pcm is not None:
            tr['pcm'] = pcm
            return pcm
        pcm, _, _ = adx_decode(tr['data'], progress=progress)
        tr['pcm'] = pcm
        self._cache_put(tr, pcm)
        return pcm

    # ---------- barra de carga + coordinador (hilo principal) ----------

    def _session_label(self, session, tr=None):
        if session['kind'] == 'folder':
            return '%d temas desde %s' % (len(session['tracks']), session.get('dir', ''))
        t0 = tr or session['tracks'][0]
        return '%s' % t0['path']

    def _loadbar_show(self, label):
        self.load_label.configure(text=label)
        self._loadbar_update(0, 1000)

    def _loadbar_update(self, done, total):
        c = self.load_canvas
        try:
            w = c.winfo_width() or 400
        except tk.TclError:
            return
        h = 14
        frac = 0 if total <= 0 else max(0.0, min(1.0, done / total))
        c.delete('all')
        c.create_rectangle(0, 0, int(w * frac), h, fill='#1a8a1a', outline='')
        c.create_text(w // 2, h // 2, text='%d%%' % int(frac * 100),
                      fill='white', font=('Consolas', 8, 'bold'))

    def _loadbar_hide(self):
        self.load_label.configure(text='')
        try:
            self.load_canvas.delete('all')
        except tk.TclError:
            pass

    def _progress_set(self, token, done, total):
        L = self._loader
        if L is not None and L['token'] == token and token == self._play_token:
            self._load_progress = (done, total)

    def _loader_start(self, token, label, kind='play'):
        self._loader = dict(token=token, label=label, kind=kind)
        self._load_progress = None
        self._load_outcome = None
        self._loadbar_show(label)
        self.root.after(80, self._loader_poll)

    def _loader_poll(self):
        L = self._loader
        if L is None:
            return
        if L['token'] != self._play_token:
            self._loader = None
            self._loadbar_hide()
            return
        if self._load_progress is not None:
            done, total = self._load_progress
            self._loadbar_update(done, total)
        oc = self._load_outcome
        if oc is not None and oc[1] == L['token']:
            kind, token, payload = oc
            self._load_outcome = None
            self._loader = None
            self._loadbar_hide()
            self._dispatch_outcome(kind, token, payload)
            return
        self.root.after(80, self._loader_poll)

    def _dispatch_outcome(self, kind, token, payload):
        if kind == 'play':
            session, tr, idx, pcm, path = payload
            if self.session is not session:
                return
            winsound.PlaySound(path,
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self._last_play = (idx, pcm, path)
            self.info_label.configure(text=self._session_label(session, tr))
            self.log('Reproduciendo: %s (%s)%s' % (
                tr['fname'], tr['name'], ' [reemplazo]' if tr['repl'] else ''))
        elif kind == 'play_err':
            self.info_label.configure(text='Error al reproducir')
            messagebox.showerror('Error', payload)
        elif kind == 'trim':
            session, tr, idx, pcm, budget = payload
            if self.session is not session:
                return
            self.info_label.configure(text=self._session_label(session, tr))
            self._open_editor_now(session, tr, idx, pcm, budget)
        elif kind == 'trim_err':
            self.info_label.configure(text='Error al abrir el editor')
            messagebox.showerror('Error', payload)
        elif kind == 'extract':
            session, tr, out = payload
            if self.session is not session:
                return
            self.info_label.configure(text=self._session_label(session, tr))
            self.log('WAV extraido: %s' % out)
        elif kind == 'extract_err':
            self.info_label.configure(text='Error al extraer')
            messagebox.showerror('Error', payload)
        elif kind == 'prep_done':
            session, bad = payload
            if self.session is not session:
                return
            self.info_label.configure(text=self._session_label(session))
            self.log('Temas listos: %d preparados.' % len(session['tracks']))
            for fname in bad:
                self.log('  AVISO: no se pudo preparar %s' % fname)
        elif kind == 'prep_err':
            self.log('ERROR preparando temas: %s' % payload)

    # ---------- reproducir ----------

    def cmd_play(self):
        idx = self._sel_idx()
        if idx is None or not winsound:
            return
        session = self.session
        tr = session['tracks'][idx]
        # 1) Cortar lo que suena AL INSTANTE (feedback inmediato).
        winsound.PlaySound(None, winsound.SND_PURGE)
        # 2) Via rapida 1: el mismo wav de la ultima vez sigue ahi.
        lp = self._last_play
        if lp is not None and lp[0] == idx and os.path.isfile(lp[2]):
            winsound.PlaySound(lp[2],
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.log('Reproduciendo: %s (%s)%s' % (
                tr['fname'], tr['name'], ' [reemplazo]' if tr['repl'] else ''))
            return
        # 3) Via rapida 2: wav en cache de disco -> directo, sin decodificar.
        if tr['repl'] is None:
            cw = self._cache_wav_path(tr)
            if cw is not None:
                winsound.PlaySound(cw,
                                   winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                self._last_play = (idx, tr['pcm'], cw)
                self.log('Reproduciendo: %s (%s)%s' % (
                    tr['fname'], tr['name'], ' [reemplazo]' if tr['repl'] else ''))
                return
        # 4) Via lenta: decodificar + volcar en 2do plano (la GUI no se congela).
        self._play_token += 1
        token = self._play_token
        self._play_cleanup(token)
        self._loader_start(token, 'Cargando: %s ...' % tr['fname'])
        t = threading.Thread(target=self._play_worker, args=(session, tr, idx, token),
                             daemon=True)
        t.start()

    def _play_cleanup(self, current_token):
        """Borra los WAV temporales de plays viejos."""
        try:
            base = os.path.basename(PREVIEW_WAV) + '.'
            d = os.path.dirname(PREVIEW_WAV)
            for f in os.listdir(d):
                if f.startswith(base) and not f.endswith('.%d.wav' % current_token):
                    try:
                        os.remove(os.path.join(d, f))
                    except Exception:
                        pass
        except Exception:
            pass

    def _play_worker(self, session, tr, idx, token):
        try:
            pcm = self._load_pcm_sync(
                tr, progress=lambda d, t: self._progress_set(token, d, t))
            if token != self._play_token:
                return
            path = '%s.%d.wav' % (PREVIEW_WAV, token)
            write_wav(path, pcm, 44100, True)
            if token != self._play_token:
                try:
                    os.remove(path)
                except Exception:
                    pass
                return
            self._load_outcome = ('play', token, (session, tr, idx, pcm, path))
        except Exception as e:
            self._load_outcome = ('play_err', token, str(e))

    def cmd_stop(self):
        if winsound:
            winsound.PlaySound(None, winsound.SND_PURGE)
        # Si habia un play todavia cargando, cancelarlo para que no
        # empiece a sonar despues del stop (la preparacion sigue sola).
        if self._loader is not None and self._loader.get('kind') == 'play':
            self._play_token += 1
            self._loader = None
            self._loadbar_hide()

    # ---------- extraer ----------

    def cmd_extract(self):
        idx = self._sel_idx()
        if idx is None:
            return
        tr = self.session['tracks'][idx]
        base = os.path.splitext(tr['fname'])[0]
        out = filedialog.asksaveasfilename(title='Guardar WAV', initialfile=base + '.wav',
                                           filetypes=[('WAV', '*.wav')],
                                           defaultextension='.wav')
        if not out:
            return
        try:
            pcm = tr['repl']['pcm'] if tr['repl'] is not None else tr['pcm']
            if pcm is not None:
                write_wav(out, pcm, 44100, True)
                self.log('WAV extraido: %s' % out)
                return
            self._play_token += 1
            token = self._play_token
            self._loader_start(token, 'Decodificando para extraer: %s ...' % tr['fname'],
                                 kind='extract')
            session = self.session
            t = threading.Thread(target=self._extract_worker,
                                 args=(session, tr, out, token), daemon=True)
            t.start()
        except Exception as e:
            messagebox.showerror('Error', str(e))

    def _extract_worker(self, session, tr, out, token):
        try:
            pcm = self._load_pcm_sync(
                tr, progress=lambda d, t: self._progress_set(token, d, t))
            if token != self._play_token:
                return
            write_wav(out, pcm, 44100, True)
            self._load_outcome = ('extract', token, (session, tr, out))
        except Exception as e:
            self._load_outcome = ('extract_err', token, str(e))

    # ---------- reemplazar ----------

    class _EncodeAbort(Exception):
        pass

    def _center_window(self, win, w, h):
        win.update_idletasks()
        try:
            px = self.root.winfo_rootx()
            py = self.root.winfo_rooty()
            pw = self.root.winfo_width()
            ph = self.root.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 3)
        except tk.TclError:
            x, y = 200, 200
        win.geometry('%dx%d+%d+%d' % (w, h, max(0, x), max(0, y)))

    def _progress_dialog(self, title, text, token, determinate=True):
        """Ventana modal CENTRADA sobre la app. La barra la mueve el
        coordinador (_encode_poll) en el hilo principal; el trabajo
        pesado corre en un worker (la GUI no se congela)."""
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.protocol('WM_DELETE_WINDOW', lambda: self._cancel_encode(token))
        tk.Label(dlg, text=text, padx=20, pady=10).pack()
        if determinate:
            bar = ttk.Progressbar(dlg, length=300, mode='determinate')
        else:
            bar = ttk.Progressbar(dlg, length=300, mode='indeterminate')
        bar.pack(padx=20, pady=4)
        status = tk.Label(dlg, text='', font=('Consolas', 9), fg='#555')
        status.pack(padx=20)
        tk.Button(dlg, text='Cancelar',
                  command=lambda: self._cancel_encode(token)).pack(pady=(4, 12))
        self._center_window(dlg, 340, 150)
        dlg.update_idletasks()
        if not determinate:
            try:
                bar.start(20)
            except tk.TclError:
                pass
        return dlg, bar, status

    def _close_encode_dialog(self):
        st = self._encode_state
        self._encode_state = None
        if not st:
            return
        try:
            st['bar'].stop()
        except Exception:
            pass
        try:
            st['dlg'].grab_release()
        except Exception:
            pass
        try:
            st['dlg'].destroy()
        except Exception:
            pass

    def _cancel_encode(self, token=None):
        st = self._encode_state
        if st is None:
            return
        if token is not None and token != st['token']:
            return
        self._encode_token += 1  # el worker lo ve y aborta; el poll cierra
        self.log('Codificacion cancelada por el usuario.')

    def _encode_poll(self):
        st = self._encode_state
        if st is None:
            return
        if st['token'] != self._encode_token:
            self._close_encode_dialog()
            return
        if self._encode_progress is not None and st['bar_mode'] == 'det':
            done, total = self._encode_progress
            frac = 0 if total <= 0 else max(0.0, min(1.0, done / total))
            try:
                st['bar']['value'] = frac * 100
                st['status'].configure(text='%d%%' % int(frac * 100))
            except tk.TclError:
                return
        oc = self._encode_outcome
        if oc is not None and oc[1] == st['token']:
            kind, token, payload = oc
            self._encode_outcome = None
            self._close_encode_dialog()
            self._dispatch_encode(kind, token, payload)
            return
        self.root.after(100, self._encode_poll)

    def cmd_replace(self):
        idx = self._sel_idx()
        if idx is None:
            return
        tr = self.session['tracks'][idx]
        fpath = filedialog.askopenfilename(
            title='Elegir reemplazo para %s' % tr['fname'],
            filetypes=[('Audio', '*.wav *.mp3 *.flac *.ogg *.oga *.m4a *.aac *.wma *.adx'),
                       ('Todos', '*.*')])
        if not fpath:
            return
        self.log('Cargando reemplazo: %s' % os.path.basename(fpath))
        self._begin_convert(idx, fpath)

    def _begin_convert(self, idx, fpath):
        """Fase 1 (worker): convertir el archivo a PCM. Dialogo centrado
        con barra indeterminada + Cancelar; la GUI sigue viva."""
        session = self.session
        self._encode_token += 1
        token = self._encode_token
        self._encode_progress = None
        self._encode_outcome = None
        dlg, bar, status = self._progress_dialog(
            'Convirtiendo reemplazo', os.path.basename(fpath), token,
            determinate=False)
        self._encode_state = dict(dlg=dlg, bar=bar, status=status,
                                  token=token, bar_mode='ind')
        self.root.after(100, self._encode_poll)
        t = threading.Thread(target=self._convert_worker,
                             args=(session, idx, fpath, token), daemon=True)
        t.start()

    def _convert_worker(self, session, idx, fpath, token):
        try:
            pcm, info = load_music_file(fpath)
            if token != self._encode_token:
                return
            self._encode_outcome = ('converted', token,
                                    (session, idx, fpath, pcm, info))
        except Exception as e:
            self._encode_outcome = ('convert_err', token, str(e))

    def _begin_encode(self, session, idx, pcm, src_label, loop_choice, loop_sel):
        """Fase 2 (worker): codificar PCM -> ADX con barra determinada."""
        tr = session['tracks'][idx]
        snap = dict(session=session, header=bytes(tr['header']), cutoff=tr['cutoff'],
                    version=tr['version'], frame_size=tr['frame_size'],
                    total=tr['total'], fname=tr['fname'])
        self._encode_token += 1
        token = self._encode_token
        self._encode_progress = None
        self._encode_outcome = None
        dlg, bar, status = self._progress_dialog(
            'Codificando %s' % tr['fname'], 'Codificando a ADX...', token,
            determinate=True)
        self._encode_state = dict(dlg=dlg, bar=bar, status=status,
                                  token=token, bar_mode='det')
        self.root.after(100, self._encode_poll)
        t = threading.Thread(target=self._encode_worker,
                             args=(snap, idx, pcm, src_label, loop_choice,
                                   loop_sel, token), daemon=True)
        t.start()

    def _encode_worker(self, snap, idx, pcm, src_label, loop_choice, loop_sel, token):
        try:
            def _cb(done, total):
                if token != self._encode_token:
                    raise MusicApp._EncodeAbort()
                self._encode_progress = (done, total)

            new_bin, desc = build_adx_music(
                snap['header'], pcm, 44100, 2, snap['cutoff'], snap['version'],
                snap['frame_size'], loop_choice=loop_choice, loop_sel=loop_sel,
                progress=_cb)
            if token != self._encode_token:
                return
            self._encode_outcome = ('encoded', token,
                                    (snap, idx, pcm, src_label, loop_choice,
                                     loop_sel, new_bin, desc))
        except MusicApp._EncodeAbort:
            self._encode_outcome = ('aborted', token, None)
        except Exception as e:
            self._encode_outcome = ('encode_err', token, str(e))

    def _dispatch_encode(self, kind, token, payload):
        if kind == 'converted':
            session, idx, fpath, pcm, info = payload
            if self.session is not session:
                return
            dur = len(pcm) / 2 / 2 / 44100
            self.log('  %s -> PCM estereo 44100 Hz, %.1f s' % (info, dur))
            self._begin_encode(session, idx, pcm, os.path.basename(fpath),
                               self.loop_choice.get(), None)
        elif kind == 'convert_err':
            messagebox.showerror('Error', payload)
        elif kind == 'encoded':
            (snap, idx, pcm, src_label, loop_choice, loop_sel,
             new_bin, desc) = payload
            self._finish_replace(snap, idx, pcm, src_label, loop_choice,
                                 loop_sel, new_bin, desc)
        elif kind == 'encode_err':
            messagebox.showerror('Error', 'Fallo la codificacion ADX:\n%s' % payload)
        elif kind == 'aborted':
            self.log('  Reemplazo cancelado.')

    def _finish_replace(self, snap, idx, pcm, src_label, loop_choice,
                        loop_sel, new_bin, desc):
        if self.session is not snap['session']:
            messagebox.showerror('Error', 'La sesion cambio durante la codificacion.')
            return
        tr = self.session['tracks'][idx]
        strict = self._is_strict(idx)
        pcm_final = pcm
        if strict:
            budget = strict_body_budget(snap['total'], snap['header'], loop_choice, loop_sel)
            body_len = adx_body_size(len(pcm) // 4, snap['frame_size'], 2)
            if body_len > budget:
                over = body_len - budget
                self.log('  MODO ESTRICTO: el reemplazo supera el original por %d B. '
                         'Abriendo el editor para recortar...' % over)
                res = self._open_editor_forced(idx, pcm, budget)
                if res is None:
                    self.log('  Reemplazo cancelado.')
                    return
                pcm_final, loop_sel_final = res
                self._begin_encode(snap['session'], idx, pcm_final,
                                   src_label + ' (editado)', loop_choice, loop_sel_final)
                return
            pad = snap['total'] - len(new_bin)
            new_bin = new_bin + b'\x00' * pad
        # validacion final: tiene que parsear como ADX real
        try:
            parse_adx_header(new_bin)
        except Exception as e:
            messagebox.showerror('Error', 'El .bin generado no es valido:\n%s' % e)
            return
        tr['repl'] = dict(data=new_bin, pcm=pcm_final, desc=desc, src=src_label)
        self._last_play = None  # el audio de este tema cambio
        self._set_row_state(idx)
        self.log('  Reemplazado (%s, %s): %d B %s' % (
            'ESTRICTO' if strict else 'LIBRE', desc, len(new_bin),
            '(mismo tamano que el original)' if strict and len(new_bin) == tr['total']
            else ('(crece %d B)' % (len(new_bin) - tr['total']))))

    def _open_editor_forced(self, idx, pcm, budget):
        tr = self.session['tracks'][idx]
        ed = WaveEditor(self.root, 'RECORTE ESTRICTO - %s (supera por %d B)'
                        % (tr['fname'],
                           adx_body_size(len(pcm) // 4, tr['frame_size'], 2) - budget),
                        pcm, 44100, max_bytes=budget, original_bytes=budget)
        return ed.run()

    def cmd_trim(self):
        idx = self._sel_idx()
        if idx is None:
            return
        session = self.session
        tr = session['tracks'][idx]
        strict = self._is_strict(idx)
        budget = None
        if strict:
            budget = strict_body_budget(tr['total'], tr['header'],
                                        self.loop_choice.get(), None)
        pcm = tr['repl']['pcm'] if tr['repl'] is not None else tr['pcm']
        if pcm is not None:
            self._open_editor_now(session, tr, idx, pcm, budget)
            return
        # Sin cache: decodificar en 2do plano y abrir el editor al terminar.
        self._play_token += 1
        token = self._play_token
        self._loader_start(token, 'Decodificando para editar: %s ...' % tr['fname'],
                             kind='trim')
        t = threading.Thread(target=self._trim_worker,
                             args=(session, tr, idx, budget, token), daemon=True)
        t.start()

    def _trim_worker(self, session, tr, idx, budget, token):
        try:
            pcm = self._load_pcm_sync(
                tr, progress=lambda d, t: self._progress_set(token, d, t))
            if token != self._play_token:
                return
            self._load_outcome = ('trim', token, (session, tr, idx, pcm, budget))
        except Exception as e:
            self._load_outcome = ('trim_err', token, str(e))

    def _open_editor_now(self, session, tr, idx, pcm, budget):
        ed = WaveEditor(self.root, 'Editar %s' % tr['fname'], pcm, 44100,
                        max_bytes=budget, original_bytes=budget)
        res = ed.run()
        if res is None:
            return
        pcm2, loop_sel2 = res
        self._begin_encode(session, idx, pcm2, 'editado en programa',
                           self.loop_choice.get(), loop_sel2)

    def cmd_unreplace(self):
        idx = self._sel_idx()
        if idx is None:
            return
        tr = self.session['tracks'][idx]
        if tr['repl'] is None:
            return
        tr['repl'] = None
        self._last_play = None
        self._set_row_state(idx)
        self.log('  %s: reemplazo quitado, vuelve al original.' % tr['fname'])

    # ---------- guardar ----------

    def cmd_save_all(self):
        if self.session is None:
            return
        replaced = [(i, tr) for i, tr in enumerate(self.session['tracks']) if tr['repl']]
        if not replaced:
            messagebox.showinfo('Nada que guardar', 'No reemplazaste ningun tema todavia.')
            return
        outdir = filedialog.askdirectory(title='Carpeta de salida para los .bin nuevos')
        if not outdir:
            return
        grew = []
        for i, tr in replaced:
            data = tr['repl']['data']
            with open(os.path.join(outdir, tr['fname']), 'wb') as f:
                f.write(data)
            if len(data) != tr['total']:
                grew.append((tr['fname'], tr['total'], len(data)))
        self.log('Guardados %d temas en %s' % (len(replaced), outdir))
        for fname, a, b in grew:
            self.log('  AVISO: %s cambio de tamano (%d -> %d B)' % (fname, a, b))
        if grew:
            messagebox.showwarning(
                'AVISO de tamano',
                'Algunos .bin cambiaron de tamano (modo LIBRE).\n'
                'La PS3 puede no aceptar archivos con distinto tamano.\n'
                'Prueba primero en RPCS3 y, si funciona, en consola real.\n\n'
                'Para tamano exacto usa el modo ESTRICTO.')
        else:
            messagebox.showinfo('Guardado',
                                'Listo: %d temas guardados con el mismo tamano original.' % len(replaced))


# ======================================================================
# CLI
# ======================================================================

def cli_info(path):
    data = open(path, 'rb').read()
    h = parse_adx_header(data)
    loop = parse_loop_block(data)
    base = os.path.splitext(os.path.basename(path))[0]
    print('archivo : %s (%d B)' % (path, len(data)))
    print('tema    : %s' % TRACK_NAMES.get(base, base))
    print('formato : ADX tipo 3, %d canal(es), %d Hz, cutoff %d, version %s' % (
        h['channels'], h['sample_rate'], h['cutoff'], hex(h['version'])))
    print('samples : %d (%.1f s)' % (h['num_samples'], h['num_samples'] / h['sample_rate']))
    if loop:
        print('loop    : si, %d -> %d (%.1f s -> %.1f s)' % (
            loop['start'], loop['end'],
            loop['start'] / h['sample_rate'], loop['end'] / h['sample_rate']))
    else:
        print('loop    : no')


def cli_extract_wav(path, out):
    data = open(path, 'rb').read()
    pcm, sr, ch = adx_decode(data)
    write_wav(out, pcm, sr, ch == 2)
    print('WAV guardado: %s (%d Hz, %s, %.1f s)' % (
        out, sr, 'stereo' if ch == 2 else 'mono', len(pcm) / 2 / ch / sr))


def main_gui():
    root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
    MusicApp(root)
    root.mainloop()


def main(argv):
    if len(argv) >= 3 and argv[1] == 'info':
        cli_info(argv[2])
    elif len(argv) >= 3 and argv[1] == 'extraer_wav':
        out = argv[3] if len(argv) >= 4 else os.path.splitext(argv[2])[0] + '.wav'
        cli_extract_wav(argv[2], out)
    elif TK_OK:
        main_gui()
    else:
        print('tkinter no esta disponible. Usa: info / extraer_wav')


if __name__ == '__main__':
    main(sys.argv)
