"""Excel (.xlsx) dışa aktarımı."""
from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import APP_COPYRIGHT, APP_TITLE
from .util import to_date

_HEAD_FILL = PatternFill("solid", fgColor="1F2A44")
_HEAD_FONT = Font(bold=True, color="FFFFFF")
_TITLE_FONT = Font(bold=True, size=13, color="1F2A44")
_THIN = Side(style="thin", color="D5DBE5")


def _txt(ws, r: int, c: int, value):
    """Hücreye metin yazar; '=' ile başlayan kullanıcı metni (cari adı vb.) formül olarak yorumlanmaz."""
    cell = ws.cell(r, c)
    if value is None or value == "":
        cell.value = None
        return cell
    cell.value = str(value)
    cell.data_type = "s"
    return cell


def _print_setup(ws, ncols: int, head_row: int | None = None) -> None:
    """Yazdırma ayarı: A4, sütun çoksa yatay; tüm sütunlar tek sayfa genişliğine sığar, başlık satırı her sayfada."""
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "landscape" if ncols > 6 else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    if head_row:
        ws.print_title_rows = f"{head_row}:{head_row}"


def _new_workbook() -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.creator = f"{APP_TITLE} · {APP_COPYRIGHT}"
    return wb


def _sheet_title(name: str, used: set) -> str:
    t = re.sub(r"[\[\]:*?/\\]", "-", name)[:31] or "Sayfa"
    base, i = t, 2
    while t in used:
        t = f"{base[:28]}-{i}"
        i += 1
    used.add(t)
    return t


def export_sheets(path, sheets: list[dict]) -> Path:
    """sheets: [{'name', 'title'?, 'headers', 'types'?, 'rows', 'totals'?}]
    types: text | money (kuruş) | date | int | num | percent"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = _new_workbook()
    used: set = set()
    for sh in sheets:
        ws = wb.create_sheet(_sheet_title(sh["name"], used))
        headers = sh["headers"]
        types = sh.get("types") or ["text"] * len(headers)
        r0 = 1
        if sh.get("title"):
            _txt(ws, 1, 1, sh["title"]).font = _TITLE_FONT
            r0 = 3
        for c, h in enumerate(headers, 1):
            cell = _txt(ws, r0, c, h)
            cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
            cell.alignment = Alignment(vertical="center")
        widths = [len(str(h)) + 2 for h in headers]
        rows = list(sh["rows"]) + ([sh["totals"]] if sh.get("totals") else [])
        for i, row in enumerate(rows, 1):
            for c, (v, t) in enumerate(zip(row, types), 1):
                cell = ws.cell(r0 + i, c)
                if v is None or v == "":
                    cell.value = None
                elif t == "money":
                    cell.value = int(v) / 100 if isinstance(v, (int, float)) else v
                    cell.number_format = '#,##0.00'
                elif t == "date":
                    d = to_date(v)
                    cell.value = d if d else v
                    cell.number_format = "DD.MM.YYYY"
                elif t == "int":
                    cell.value = v
                elif t == "num":
                    cell.value = v
                    cell.number_format = '#,##0.00' if isinstance(v, float) else '#,##0'
                elif t == "percent":
                    cell.value = v
                    cell.number_format = "0.0%"
                else:
                    cell.value = str(v)
                    cell.data_type = "s"          # '=' ile başlayan metin formül sayılmasın
                cell.border = Border(bottom=_THIN)
                shown = 12 if t in ("money", "date") else len(str(v if v is not None else ""))
                widths[c - 1] = max(widths[c - 1], min(shown + 2, 60))
            if sh.get("totals") and i == len(rows):
                for c in range(1, len(headers) + 1):
                    ws.cell(r0 + i, c).font = Font(bold=True)
                    ws.cell(r0 + i, c).fill = PatternFill("solid", fgColor="E8ECF3")
        for c, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.freeze_panes = ws.cell(r0 + 1, 1)
        if sh["rows"]:
            ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(headers))}{r0 + len(sh['rows'])}"
        _print_setup(ws, len(headers), r0)
    if not wb.sheetnames:
        wb.create_sheet("Boş")
    wb.save(path)
    return path


# =========================================================================== rapor (bloklu) aktarımı
_FORMATS = {"money": '#,##0.00', "smoney": '#,##0.00;[Red]-#,##0.00', "dmoney": '+#,##0.00;[Red]-#,##0.00;0.00',
            "balance": '#,##0.00;[Red]-#,##0.00', "int": '#,##0', "percent": '0.0%',
            "dpercent": '+0.0%;[Red]-0.0%;0.0%', "dpp": '+0.0" puan";[Red]-0.0" puan";0.0" puan"', "num": '#,##0.00'}
_SUB_FILL = PatternFill("solid", fgColor="F3F5F9")
_TONE_FONT = {"GELIR": "166534", "GIDER": "9F1239", "VIRMAN": "92400E", "CARI": "5B21B6", "muted": "6B7280"}


def _write_cell(ws, r: int, c: int, v, kind: str):
    from .report_model import unwrap
    raw, k = unwrap(v)
    kind = k or kind
    cell = ws.cell(r, c)
    if raw is None or raw == "":
        cell.value = None
        return cell
    if kind in ("money", "smoney", "dmoney", "balance"):
        cell.value = int(raw) / 100 if isinstance(raw, (int, float)) else raw
    elif kind == "dpp":
        cell.value = raw * 100 if isinstance(raw, (int, float)) else raw
    elif kind == "date":
        d = to_date(raw)
        cell.value = d if d else raw
        cell.number_format = "DD.MM.YYYY"
        return cell
    elif kind in ("int", "percent", "dpercent", "num"):
        cell.value = raw
    else:
        cell.value = str(raw)
        cell.data_type = "s"            # '=' ile başlayan metin formül sayılmasın
        return cell
    if kind in _FORMATS:
        cell.number_format = _FORMATS[kind]
    return cell


def export_report(path, rep) -> Path:
    """report_model.Report -> .xlsx. Özet blokları 'Özet' sayfasına, her tablo kendi sayfasına yazılır."""
    from .report_model import KV, Table, Text, flatten
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = _new_workbook()
    used: set = set()
    blocks = flatten(rep.blocks)
    head_lines = [x for x in rep.lines if x]

    summary = [b for b in blocks if isinstance(b, (KV, Text))]
    if summary:
        ws = wb.create_sheet(_sheet_title("Özet", used))
        _txt(ws, 1, 1, rep.title).font = _TITLE_FONT
        r = 2
        for line in head_lines:
            _txt(ws, r, 1, line).font = Font(color="6B7280")
            r += 1
        r += 1
        for b in summary:
            _txt(ws, r, 1, b.title).font = Font(bold=True, color="1F2A44", size=12)
            r += 1
            if isinstance(b, KV):
                for lab, val, kind in b.items:
                    _txt(ws, r, 1, lab).border = Border(bottom=_THIN)
                    _write_cell(ws, r, 2, val, kind).border = Border(bottom=_THIN)
                    r += 1
            else:
                for _, text in b.items:
                    _txt(ws, r, 1, text)
                    r += 1
            r += 1
        ws.column_dimensions["A"].width = 46
        ws.column_dimensions["B"].width = 22
        _print_setup(ws, 2)

    for b in blocks:
        if not isinstance(b, Table):
            continue
        ws = wb.create_sheet(_sheet_title(b.sheet or b.title, used))
        _txt(ws, 1, 1, f"{rep.title} · {b.title}").font = _TITLE_FONT
        r0 = 2
        for line in head_lines:
            _txt(ws, r0, 1, line).font = Font(color="6B7280")
            r0 += 1
        r0 += 1
        headers = [h + (" (+B / −A)" if t == "balance" else "") for h, t in zip(b.headers, b.types)]
        for c, h in enumerate(headers, 1):
            cell = _txt(ws, r0, c, h)
            cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        widths = [min(max(len(str(h)) + 2, 8), 40) for h in headers]
        for i, row in enumerate(b.rows, 1):
            tone = b.tones[i - 1] if b.tones and i - 1 < len(b.tones) else None
            for c, kind in enumerate(b.types, 1):
                v = row[c - 1] if c - 1 < len(row) else None
                cell = _write_cell(ws, r0 + i, c, v, kind)
                cell.border = Border(bottom=_THIN)
                if tone in ("bold", "sub"):
                    cell.font = Font(bold=True)
                    if tone == "sub":
                        cell.fill = _SUB_FILL
                elif tone in _TONE_FONT and (b.tone_cols is None or (c - 1) in b.tone_cols):
                    cell.font = Font(color=_TONE_FONT[tone])
                shown = 13 if kind in ("money", "smoney", "dmoney", "balance", "date") else \
                    len(str(cell.value if cell.value is not None else ""))
                widths[c - 1] = max(widths[c - 1], min(shown + 2, 60))
        if b.total and b.rows:
            rt = r0 + len(b.rows) + 1
            for c, kind in enumerate(b.types, 1):
                v = b.total[c - 1] if c - 1 < len(b.total) else None
                cell = _write_cell(ws, rt, c, v, kind)
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="E8ECF3")
        if b.note:
            _txt(ws, r0 + len(b.rows) + (3 if b.total and b.rows else 2), 1, b.note).font = Font(italic=True,
                                                                                                    color="6B7280")
        if not b.rows:
            _txt(ws, r0 + 1, 1, b.empty).font = Font(italic=True, color="6B7280")
        for c, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.freeze_panes = ws.cell(r0 + 1, 1)
        if b.rows:
            ws.auto_filter.ref = f"A{r0}:{get_column_letter(len(headers))}{r0 + len(b.rows)}"
        _print_setup(ws, len(headers), r0)
    if not wb.sheetnames:
        wb.create_sheet("Boş")
    wb.save(path)
    return path
