"""Direct 50x30mm Xprinter label output via Windows RAW spooler + TSPL bitmap.

Designed for XP-D4602B (203 DPI). 50x30mm = 400x240 dots.
This deliberately bypasses browser/PDF print scaling and the Windows driver's USER paper size.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib
import sys
from typing import Any

LABEL_W = 400
LABEL_H = 240
WIDTH_BYTES = LABEL_W // 8


def _text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _win_only():
    if sys.platform != "win32":
        raise RuntimeError("라벨 직접 인쇄는 Windows ERP PC에서만 사용할 수 있습니다.")


def _gdi():
    _win_only()
    return ctypes.WinDLL("gdi32", use_last_error=True), ctypes.WinDLL("user32", use_last_error=True)


def _make_font(gdi32, height: int, weight: int = 400, face: str = "Malgun Gothic"):
    fn = gdi32.CreateFontW
    fn.restype = wintypes.HANDLE
    return fn(-abs(int(height)), 0, 0, 0, weight, 0, 0, 0, 129, 0, 0, 4, 0, face)


def _measure_text(gdi32, hdc, text: str):
    class SIZE(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]
    s = SIZE()
    gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(s))
    return int(s.cx), int(s.cy)


def _fit_font(gdi32, hdc, text: str, max_width: int, start: int, minimum: int):
    for px in range(start, minimum - 1, -1):
        font = _make_font(gdi32, px)
        old = gdi32.SelectObject(hdc, font)
        w, _ = _measure_text(gdi32, hdc, text)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)
        if w <= max_width:
            return px
    return minimum


def _draw_center(gdi32, hdc, text: str, y: int, px: int, weight: int = 400):
    if not text:
        return
    font = _make_font(gdi32, px, weight)
    old = gdi32.SelectObject(hdc, font)
    w, h = _measure_text(gdi32, hdc, text)
    gdi32.TextOutW(hdc, max(0, (LABEL_W - w)//2), y, text, len(text))
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(font)


def _render_label(row: dict[str, Any]) -> bytes:
    gdi32, _ = _gdi()

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    hdc = gdi32.CreateCompatibleDC(0)
    if not hdc:
        raise OSError(ctypes.get_last_error(), "CreateCompatibleDC 실패")

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = LABEL_W
    bmi.bmiHeader.biHeight = -LABEL_H
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), 0, 0)
    if not hbmp:
        gdi32.DeleteDC(hdc)
        raise OSError(ctypes.get_last_error(), "CreateDIBSection 실패")

    oldbmp = gdi32.SelectObject(hdc, hbmp)
    try:
        gdi32.PatBlt(hdc, 0, 0, LABEL_W, LABEL_H, 0x00FF0062)  # WHITENESS
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
        gdi32.SetTextColor(hdc, 0x000000)

        barcode_mod = importlib.import_module("rg_barcode_print_v09193")
        code = _text(row.get("바코드"))
        values = barcode_mod._code128_values(code)

        patterns = barcode_mod._CODE128_PATTERNS
        modules = 20 + sum(sum(int(c) for c in patterns[v]) for v in values)
        left, right = 16, LABEL_W - 16
        bar_top, bar_bottom = 8, 96
        usable = right - left
        scale = usable / modules
        x = left + 10 * scale
        for val in values:
            bar = True
            for ch in patterns[val]:
                mw = int(ch) * scale
                if bar:
                    x0 = int(round(x))
                    x1 = int(round(x + mw))
                    if x1 <= x0:
                        x1 = x0 + 1
                    gdi32.PatBlt(hdc, x0, bar_top, x1-x0, bar_bottom-bar_top, 0x00000042)  # BLACKNESS
                x += mw
                bar = not bar

        _draw_center(gdi32, hdc, code, 100, 18)

        product = _text(row.get("상품명")) or _text(row.get("ERP 상품명"))
        psize = _fit_font(gdi32, hdc, product, LABEL_W-24, 24, 14)
        _draw_center(gdi32, hdc, product, 127, psize)

        option = _text(row.get("옵션명"))
        if option:
            osize = _fit_font(gdi32, hdc, option, LABEL_W-24, 19, 13)
            _draw_center(gdi32, hdc, option, 158, osize)

        _draw_center(gdi32, hdc, "MADE IN CHINA", 205, 24)

        size = LABEL_W * LABEL_H * 4
        raw = ctypes.string_at(bits, size)

        out = bytearray(WIDTH_BYTES * LABEL_H)
        for y in range(LABEL_H):
            row_off = y * LABEL_W * 4
            dst = y * WIDTH_BYTES
            for xpix in range(LABEL_W):
                p = row_off + xpix*4
                b, g, r = raw[p], raw[p+1], raw[p+2]
                if (int(r)+int(g)+int(b)) < 384:
                    out[dst + (xpix >> 3)] |= 0x80 >> (xpix & 7)
        return bytes(out)
    finally:
        gdi32.SelectObject(hdc, oldbmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc)


def _raw_print(printer_name: str, payload: bytes, doc_name: str = "ERP 50x30 Barcode Labels"):
    _win_only()
    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR), ("pDatatype", wintypes.LPWSTR)]

    hprinter = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer_name, ctypes.byref(hprinter), None):
        raise OSError(ctypes.get_last_error(), f"프린터를 열 수 없습니다: {printer_name}")
    try:
        info = DOC_INFO_1(doc_name, None, "RAW")
        if not winspool.StartDocPrinterW(hprinter, 1, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "인쇄 작업 시작 실패")
        try:
            winspool.StartPagePrinter(hprinter)
            try:
                buf = ctypes.create_string_buffer(payload)
                written = wintypes.DWORD()
                if not winspool.WritePrinter(hprinter, buf, len(payload), ctypes.byref(written)):
                    raise OSError(ctypes.get_last_error(), "프린터 데이터 전송 실패")
                if written.value != len(payload):
                    raise RuntimeError(f"프린터 전송 불완전: {written.value}/{len(payload)} bytes")
            finally:
                winspool.EndPagePrinter(hprinter)
        finally:
            winspool.EndDocPrinter(hprinter)
    finally:
        winspool.ClosePrinter(hprinter)


def list_printers() -> list[str]:
    _win_only()
    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    flags = 0x00000002 | 0x00000004  # LOCAL | CONNECTIONS
    needed = wintypes.DWORD()
    returned = wintypes.DWORD()
    winspool.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if not needed.value:
        return []

    buf = ctypes.create_string_buffer(needed.value)
    if not winspool.EnumPrintersW(flags, None, 4, buf, needed.value, ctypes.byref(needed), ctypes.byref(returned)):
        raise OSError(ctypes.get_last_error(), "프린터 목록 조회 실패")

    class PRINTER_INFO_4(ctypes.Structure):
        _fields_ = [("pPrinterName", wintypes.LPWSTR), ("pServerName", wintypes.LPWSTR), ("Attributes", wintypes.DWORD)]

    arr = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4))
    names = []
    for i in range(returned.value):
        if arr[i].pPrinterName:
            names.append(arr[i].pPrinterName)
    return sorted(set(names), key=str.lower)


def print_labels(rows: list[dict[str, Any]], printer_name: str) -> int:
    if not printer_name:
        raise ValueError("프린터를 선택해 주세요.")
    total = 0
    chunks = []
    for row in rows:
        qty = int(row.get("출력수량") or 0)
        if qty < 1:
            continue
        bitmap = _render_label(row)
        cmd = (
            b"SIZE 50 mm,30 mm\r\n"
            b"GAP 2 mm,0 mm\r\n"
            b"DIRECTION 1\r\n"
            b"CLS\r\n"
            + f"BITMAP 0,0,{WIDTH_BYTES},{LABEL_H},0,".encode("ascii")
            + bitmap
            + b"\r\n"
            + f"PRINT {qty},1\r\n".encode("ascii")
        )
        chunks.append(cmd)
        total += qty
    if total < 1:
        raise ValueError("인쇄할 라벨이 없습니다.")
    _raw_print(printer_name, b"".join(chunks))
    return total
