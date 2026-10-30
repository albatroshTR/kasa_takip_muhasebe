"""PDF raporları: günlük kasa, cari ekstre, cari bakiye listesi, kılavuz ve Raporlar bölümünün genel çizicisi."""
from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import CondPageBreak, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import APP_COPYRIGHT, APP_TITLE, __version__
from .paths import resource_path
from .util import (balance_word, fmt_balance, fmt_compact, fmt_date, fmt_dt, fmt_money, fmt_percent, now_iso,
                   today, tr_upper)

NAVY = colors.HexColor("#1F2A44")
SUBHEAD = colors.HexColor("#F3F4F6")
GRID = colors.HexColor("#D5DBE5")
MUTED = colors.HexColor("#6B7280")
TOTAL_BG = colors.HexColor("#E8ECF3")
SUBTOTAL_BG = colors.HexColor("#F3F5F9")
GREEN = colors.HexColor("#166534")
RED = colors.HexColor("#9F1239")
AMBER = colors.HexColor("#92400E")
PURPLE = colors.HexColor("#5B21B6")
BAR_GELIR = colors.HexColor("#2F855A")
BAR_GIDER = colors.HexColor("#C0392B")

PAGE_W, PAGE_H = A4
CAT_SIDE_BY_SIDE_MAX = 18      # daha uzun kategori listeleri alt alta basılır
MARGIN = 14 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_FONT: tuple | None = None


def _fonts() -> tuple[str, str]:
    """Windows'ta Times New Roman, yoksa programla gelen Liberation Serif kullanılır (Türkçe karakterli)."""
    global _FONT
    if _FONT:
        return _FONT
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [
        (windir / "times.ttf", windir / "timesbd.ttf"),
        (resource_path("assets", "fonts", "LiberationSerif-Regular.ttf"),
         resource_path("assets", "fonts", "LiberationSerif-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
         Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")),
    ]
    for reg, bold in candidates:
        try:
            if reg.exists() and bold.exists():
                pdfmetrics.registerFont(TTFont("KTSerif", str(reg)))
                pdfmetrics.registerFont(TTFont("KTSerif-Bold", str(bold)))
                pdfmetrics.registerFontFamily("KTSerif", normal="KTSerif", bold="KTSerif-Bold",
                                              italic="KTSerif", boldItalic="KTSerif-Bold")
                _FONT = ("KTSerif", "KTSerif-Bold")
                return _FONT
        except Exception:
            continue
    _FONT = ("Helvetica", "Helvetica-Bold")
    return _FONT


def _st() -> dict:
    reg, bold = _fonts()
    return {
        "firma": ParagraphStyle("firma", fontName=bold, fontSize=15, leading=18, textColor=NAVY),
        "firma_alt": ParagraphStyle("firma_alt", fontName=reg, fontSize=8.5, leading=11, textColor=MUTED),
        "baslik": ParagraphStyle("baslik", fontName=bold, fontSize=14, leading=17, textColor=NAVY, alignment=TA_RIGHT),
        "baslik_alt": ParagraphStyle("baslik_alt", fontName=reg, fontSize=9.5, leading=12.5, alignment=TA_RIGHT),
        "bolum": ParagraphStyle("bolum", fontName=bold, fontSize=10.5, leading=13, textColor=colors.white),
        "hucre": ParagraphStyle("hucre", fontName=reg, fontSize=8.6, leading=10.4),
        "hucre_k": ParagraphStyle("hucre_k", fontName=reg, fontSize=8, leading=9.6, textColor=MUTED),
        "metin": ParagraphStyle("metin", fontName=reg, fontSize=9.5, leading=13),
        "metin_b": ParagraphStyle("metin_b", fontName=bold, fontSize=10, leading=13),
        "imza": ParagraphStyle("imza", fontName=bold, fontSize=9.5, leading=12, alignment=TA_CENTER),
        "imza_alt": ParagraphStyle("imza_alt", fontName=reg, fontSize=8, leading=10, alignment=TA_CENTER,
                                   textColor=MUTED),
        "bos": ParagraphStyle("bos", fontName=reg, fontSize=9, leading=12, textColor=MUTED, alignment=TA_LEFT),
    }


def _m(k, plus=False, dash_zero=False) -> str:
    if k is None:
        return "-"
    if dash_zero and not k:
        return "-"
    return fmt_money(k, plus=plus)


def _p(text, style="hucre") -> Paragraph:
    return Paragraph(escape(str(text or "")), _st()[style])


# --------------------------------------------------------------------------- sayfa altlığı
def _canvas_factory(footer_left: str, page_w: float = PAGE_W):
    class _NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._footer(total)
                super().showPage()
            super().save()

        def _footer(self, total):
            reg, _ = _fonts()
            self.saveState()
            self.setStrokeColor(GRID)
            self.setLineWidth(0.5)
            self.line(MARGIN, 11 * mm, page_w - MARGIN, 11 * mm)
            self.setFont(reg, 7.5)
            self.setFillColor(MUTED)
            self.drawString(MARGIN, 7 * mm, footer_left)
            self.drawRightString(page_w - MARGIN, 7 * mm, f"Sayfa {self._pageNumber} / {total}")
            self.restoreState()

    return _NumberedCanvas


def _build(path, story, firma: dict, kullanici: str, title: str, pagesize=A4) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    footer = " · ".join(x for x in [firma.get("adi") or "", f"{APP_TITLE} {__version__}",
                                    f"Oluşturma: {fmt_dt(now_iso())}", kullanici or ""] if x)
    doc = SimpleDocTemplate(str(path), pagesize=pagesize, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=12 * mm,
                            bottomMargin=16 * mm, title=title, author=firma.get("adi") or APP_TITLE,
                            creator=f"{APP_TITLE} {__version__} · {APP_COPYRIGHT}")
    doc.build(story, canvasmaker=_canvas_factory(footer, pagesize[0]))
    return path


# --------------------------------------------------------------------------- yapı taşları
def _header(firma: dict, title: str, lines: list[str], width: float = CONTENT_W) -> list:
    st = _st()
    left = [Paragraph(escape(firma.get("adi") or "Firma adı tanımlanmamış"), st["firma"])]
    alt = " · ".join(x for x in [firma.get("adres"), f"Tel: {firma['telefon']}" if firma.get("telefon") else "",
                                 firma.get("vergi")] if x)
    if alt:
        left.append(Paragraph(escape(alt), st["firma_alt"]))
    right = [Paragraph(escape(tr_upper(title)), st["baslik"])]
    right += [Paragraph(escape(x), st["baslik_alt"]) for x in lines if x]
    t = Table([[left, right]], colWidths=[width * 0.55, width * 0.45])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, NAVY),
    ]))
    return [t, Spacer(1, 4 * mm)]


def _section(title: str, width: float = CONTENT_W, keep: bool = True) -> Table:
    t = Table([[Paragraph(escape(tr_upper(title)), _st()["bolum"])]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
    ]))
    t.keepWithNext = 1 if keep else 0     # bölüm başlığı sayfa sonunda tek başına kalmasın
    return t


def _long_section(title: str, width: float = CONTENT_W) -> list:
    """Uzun (sayfalara bölünebilen) tablo öncesi başlık: sayfada yer azsa yeni sayfadan başlar, yoksa tablo
    hemen başlığın altından başlayıp sayfalara bölünür (keepWithNext uzun tabloyu komple sonraki sayfaya atar)."""
    return [CondPageBreak(38 * mm), _section(title, width, keep=False)]


def _grid(header, rows, widths, aligns, total=None, font_size=8.6, colors_=None, bold_rows=(), bg_rows=(),
          split_in_row: bool = False) -> Table:
    reg, bold = _fonts()
    data = [header] + rows + ([total] if total else [])
    t = Table(data, colWidths=widths, repeatRows=1, splitInRow=1 if split_in_row else 0)
    last_body = len(data) - (2 if total else 1)
    cmds = [
        ("FONT", (0, 0), (-1, -1), reg, font_size),
        ("FONT", (0, 0), (-1, 0), bold, font_size),
        ("BACKGROUND", (0, 0), (-1, 0), SUBHEAD),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i in range(1, last_body + 1):
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.3, GRID))
    for c, a in enumerate(aligns):
        cmds.append(("ALIGN", (c, 0), (c, -1), {"L": "LEFT", "R": "RIGHT", "C": "CENTER"}[a]))
    if total:
        cmds += [("FONT", (0, -1), (-1, -1), bold, font_size), ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
                 ("LINEABOVE", (0, -1), (-1, -1), 0.8, NAVY)]
    for r in bold_rows:
        cmds.append(("FONT", (0, r), (-1, r), bold, font_size))
    for r in bg_rows:
        cmds.append(("BACKGROUND", (0, r), (-1, r), SUBTOTAL_BG))
    for (c, r, color) in colors_ or []:
        cmds.append(("TEXTCOLOR", (c, r), (c, r), color))
    t.setStyle(TableStyle(cmds))
    return t


def _block(title: str, inner, width: float) -> Table:
    """Bölüm başlığı + içerik tablosunu tek blok halinde (yan yana yerleşim için)."""
    t = Table([[_section(title, width)], [inner]], colWidths=[width])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _side_by_side(left, right, gap: float = 6 * mm) -> Table:
    half = (CONTENT_W - gap) / 2
    t = Table([[left, "", right]], colWidths=[half, gap, half])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _kv_table(pairs: list[tuple[str, str]], cols: int = 2, value_align="RIGHT", width: float = CONTENT_W,
              value_colors: dict | None = None) -> Table:
    """Etiket/değer çiftlerini cols sütun halinde dizer."""
    reg, bold = _fonts()
    rows = []
    for i in range(0, len(pairs), cols):
        row = []
        for lab, val in pairs[i:i + cols]:
            row += [lab, val]
        row += ["", ""] * (cols - len(pairs[i:i + cols]))
        rows.append(row)
    lw, vw = width / cols * 0.58, width / cols * 0.42
    t = Table(rows, colWidths=[lw, vw] * cols)
    cmds = [("FONT", (0, 0), (-1, -1), reg, 9.2), ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]
    for c in range(cols):
        cmds += [("FONT", (2 * c + 1, 0), (2 * c + 1, -1), bold, 9.6),
                 ("ALIGN", (2 * c + 1, 0), (2 * c + 1, -1), value_align),
                 ("TEXTCOLOR", (2 * c, 0), (2 * c, -1), colors.HexColor("#374151"))]
    for r in range(len(rows)):
        cmds.append(("LINEBELOW", (0, r), (-1, r), 0.3, GRID))
    for (i, color) in (value_colors or {}).items():
        cmds.append(("TEXTCOLOR", (2 * (i % cols) + 1, i // cols), (2 * (i % cols) + 1, i // cols), color))
    t.setStyle(TableStyle(cmds))
    return t


def _signatures(labels: list[str], width: float = CONTENT_W) -> KeepTogether:
    st = _st()
    w = width / len(labels)
    t = Table([[Paragraph(escape(x), st["imza"]) for x in labels],
               ["" for _ in labels],
               [Paragraph("Ad Soyad / İmza", st["imza_alt"]) for _ in labels]],
              colWidths=[w] * len(labels), rowHeights=[7 * mm, 18 * mm, 6 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.4, GRID),
                           ("BACKGROUND", (0, 0), (-1, 0), SUBHEAD), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return KeepTogether([Spacer(1, 7 * mm), t])


def _w(*parts) -> list[float]:
    """Oranları içerik genişliğine ölçekler."""
    total = sum(parts)
    return [CONTENT_W * p / total for p in parts]


# --------------------------------------------------------------------------- günlük kasa
def daily_kasa_pdf(path, rep: dict, firma: dict, kullanici: str = "", imza: bool = True) -> Path:
    st = _st()
    tarih = rep["tarih"]
    if rep.get("kapanis"):
        k = rep["kapanis"]
        durum = f"Durum: Kapalı · {fmt_dt(k['kapanis_zamani'])}" + (f" · {k.get('kapatan')}" if k.get("kapatan") else "")
    elif rep.get("kilitli"):
        durum = "Durum: Kilitli (sonraki bir gün kapatıldı)"
    else:
        durum = "Durum: Açık (gün kapatılmadı)"
    story = _header(firma, "Günlük Kasa Raporu", [fmt_date(tarih, weekday=True), durum])

    # Özet
    story.append(_section("Günlük Özet"))
    story.append(_kv_table([
        ("Toplam Gelir", _m(rep["gelir"])), ("Gelir İşlem Sayısı", str(rep["gelir_adet"])),
        ("Toplam Gider", _m(rep["gider"])), ("Gider İşlem Sayısı", str(rep["gider_adet"])),
        ("Net (Gelir − Gider)", _m(rep["net"])), ("Toplam İşlem", str(rep["gelir_adet"] + rep["gider_adet"])),
        ("Ortalama Gelir (işlem başına)", _m(rep["ort_gelir"])),
        ("Ortalama Gider (işlem başına)", _m(rep["ort_gider"])),
    ]))
    story.append(Spacer(1, 4 * mm))

    # Ödeme tipi kırılımı
    story.append(_section("Ödeme Tipi Kırılımı"))
    has_count = any(r["sayilan"] is not None for r in rep["odeme"])
    header = ["Ödeme Tipi", "Devir", "Gelir", "Gider", "Virman", "Gün Sonu"]
    widths = [30, 22, 21, 21, 19, 23]
    if has_count:
        header += ["Sayım", "Fark"]
        widths += [22, 18]
    rows, col = [], []
    for i, r in enumerate(rep["odeme"], 1):
        row = [r["ad"], _m(r["devir"]), _m(r["gelir"], dash_zero=True), _m(r["gider"], dash_zero=True),
               _m(r["virman_net"], plus=True, dash_zero=True), _m(r["gun_sonu"])]
        if has_count:
            row += [_m(r["sayilan"]) if r["sayilan"] is not None else "-",
                    (_m(r["fark"], plus=True) if r["fark"] else "0,00") if r["fark"] is not None else "-"]
            if r["fark"]:
                col.append((7, i, GREEN if r["fark"] > 0 else RED))
        if r["gun_sonu"] < 0:
            col.append((5, i, RED))
        rows.append(row)
    tot = ["TOPLAM", _m(sum(r["devir"] for r in rep["odeme"])), _m(rep["gelir"]), _m(rep["gider"]),
           _m(sum(r["virman_net"] for r in rep["odeme"]), plus=True, dash_zero=True),
           _m(sum(r["gun_sonu"] for r in rep["odeme"]))]
    if has_count:
        counted = [r for r in rep["odeme"] if r["sayilan"] is not None]
        tot += [_m(sum(r["sayilan"] for r in counted)) if counted else "-",
                _m(sum(r["fark"] for r in counted), plus=True) if counted else "-"]
    story.append(_grid(header, rows, _w(*widths), ["L"] + ["R"] * (len(header) - 1), total=tot, colors_=col))
    story.append(Spacer(1, 4 * mm))

    # Kategoriler: kısa listeler yan yana, uzun listeler alt alta (sayfaya bölünebilsin diye)
    lists = [("Gelir Kategorileri", rep["kat_gelir"], rep["gelir"]), ("Gider Kategorileri", rep["kat_gider"], rep["gider"])]
    stacked = max(len(rep["kat_gelir"]), len(rep["kat_gider"])) > CAT_SIDE_BY_SIDE_MAX

    def cat_grid(items, total, width):
        body = [[x["ad"], str(x["adet"]) if x["adet"] else "-", _m(x["toplam"], dash_zero=True)] for x in items]
        if not body:
            body = [["Kayıt yok", "", ""]]
        return _grid(["Kategori", "Adet", "Toplam"], body, [width * 0.56, width * 0.14, width * 0.30],
                     ["L", "C", "R"], total=["TOPLAM", str(sum(x["adet"] for x in items)), _m(total)])

    if stacked:
        for title, items, total in lists:
            story += [_section(title), cat_grid(items, total, CONTENT_W), Spacer(1, 4 * mm)]
    else:
        half = (CONTENT_W - 6 * mm) / 2
        blocks = [_block(title, cat_grid(items, total, half), half) for title, items, total in lists]
        story.append(_side_by_side(*blocks))
        story.append(Spacer(1, 4 * mm))

    # Hareket dökümü
    hs = rep["hareketler"]
    story += _long_section("Hareket Dökümü") if len(hs) > 12 else [_section("Hareket Dökümü")]
    if hs:
        rows, col = [], []
        tcolor = {"GELIR": GREEN, "GIDER": RED, "VIRMAN": AMBER, "CARI_BORC": PURPLE, "CARI_ALACAK": PURPLE}
        for i, m in enumerate(hs, 1):
            rows.append([str(i), m["saat"] or "", m["tur_adi"], _p(m["kategori_gosterim"]),
                         _p(m["odeme_gosterim"]), _p(m["cari"]), _p(m["aciklama"] or ""),
                         _m(m["tutar"])])
            col += [(2, i, tcolor.get(m["tur"], colors.black)), (7, i, tcolor.get(m["tur"], colors.black))]
        story.append(_grid(["No", "Saat", "Tür", "Kategori", "Ödeme", "Cari", "Açıklama", "Tutar"], rows,
                           _w(7, 10, 16, 27, 22, 30, 46, 22), ["C", "C", "L", "L", "L", "L", "L", "R"],
                           colors_=col))
        if rep.get("cari_fis_adet"):
            story.append(Spacer(1, 1.5 * mm))
            story.append(Paragraph("Cari Borç / Cari Alacak fişleri kasa bakiyelerini etkilemez; yalnızca cari "
                                   "hesaplara işlenir.", st["hucre_k"]))
    else:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("Bu tarihte kayıt bulunmuyor.", st["bos"]))

    if rep.get("kapanis") and rep["kapanis"].get("aciklama"):
        story.append(Spacer(1, 4 * mm))
        story.append(KeepTogether([_section("Kapanış Notu"), Spacer(1, 1.5 * mm),
                                   Paragraph(escape(rep["kapanis"]["aciklama"]), st["metin"])]))

    if imza:
        story.append(_signatures(["Teslim Eden", "Teslim Alan", "Kontrol Eden"]))
    return _build(path, story, firma, kullanici, f"Günlük Kasa Raporu {fmt_date(tarih)}")


# --------------------------------------------------------------------------- cari ekstre
def cari_ekstre_pdf(path, cari: dict, st_: dict, start, end, firma: dict, kullanici: str = "") -> Path:
    st = _st()
    story = _header(firma, "Cari Hesap Ekstresi", [f"Dönem: {fmt_date(start)} – {fmt_date(end)}",
                                                   f"Düzenleme tarihi: {fmt_date(today())}"])
    vd = " / ".join(x for x in [cari.get("vergi_dairesi"), cari.get("vergi_no")] if x)
    story.append(_section("Cari Bilgileri"))
    story.append(_kv_table([
        ("Cari Kodu", cari.get("kod") or "-"), ("Cari Tipi", cari.get("tip_adi") or "-"),
        ("Ünvan", cari.get("unvan") or "-"), ("Telefon", cari.get("telefon") or "-"),
        ("Vergi Dairesi / No", vd or "-"), ("Yetkili", cari.get("yetkili") or "-"),
    ], value_align="LEFT"))
    if cari.get("adres"):
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph("<b>Adres:</b> " + escape(cari["adres"]), st["metin"]))
    story.append(Spacer(1, 4 * mm))

    story += _long_section("Hesap Hareketleri") if len(st_["satirlar"]) > 12 else [_section("Hesap Hareketleri")]
    rows = [[fmt_date(start), "Devir", "", _p("Önceki dönemden devreden bakiye"), "", "",
             fmt_balance(st_["devir"])]]
    for m in st_["satirlar"]:
        rows.append([fmt_date(m["tarih"]), _p(m["islem"]), m["belge_no"] or "", _p(m["aciklama"]),
                     _m(m["borc"], dash_zero=True), _m(m["alacak"], dash_zero=True), fmt_balance(m["bakiye"])])
    total = ["", "Dönem Toplamı", "", "", _m(st_["toplam_borc"]), _m(st_["toplam_alacak"]),
             fmt_balance(st_["bakiye"])]
    story.append(_grid(["Tarih", "İşlem", "Belge No", "Açıklama", "Borç", "Alacak", "Bakiye"], rows,
                       _w(20, 28, 18, 52, 21, 21, 24), ["C", "L", "L", "L", "R", "R", "R"], total=total,
                       bold_rows=(1,)))
    story.append(Spacer(1, 4 * mm))
    b = st_["bakiye"]
    sonuc = (f"{fmt_date(end)} itibarıyla bakiye: <b>{fmt_money(abs(b))} TL</b> "
             f"({balance_word(b)}{' — cari bize borçlu' if b > 0 else (' — cariye borçluyuz' if b < 0 else '')})")
    story.append(Paragraph(sonuc, st["metin"]))
    story.append(_signatures([firma.get("adi") or "Firma Yetkilisi", cari.get("unvan") or "Cari Yetkilisi"]))
    return _build(path, story, firma, kullanici, f"Cari Ekstre {cari.get('unvan', '')}")


# --------------------------------------------------------------------------- cari listesi
def cari_listesi_pdf(path, rows: list[dict], firma: dict, kullanici: str = "", alt_baslik: str = "") -> Path:
    story = _header(firma, "Cari Bakiye Listesi", [f"Tarih: {fmt_date(today())}", alt_baslik])
    body, col = [], []
    for i, c in enumerate(rows, 1):
        body.append([c.get("kod") or "", _p(c["unvan"]), c["tip_adi"], c.get("telefon") or "",
                     _m(c["borc"], dash_zero=True), _m(c["alacak"], dash_zero=True), fmt_balance(c["bakiye"]),
                     c["durum"]])
        if c["bakiye"]:
            col.append((7, i, RED if c["bakiye"] < 0 else GREEN))
    net = sum(c["bakiye"] for c in rows)
    total = ["", f"{len(rows)} cari", "", "", _m(sum(c["borc"] for c in rows)),
             _m(sum(c["alacak"] for c in rows)), fmt_balance(net), ""]
    story.append(_grid(["Kod", "Ünvan", "Tip", "Telefon", "Borç", "Alacak", "Bakiye", "Durum"], body,
                       _w(14, 50, 22, 24, 20, 20, 24, 15), ["L", "L", "L", "L", "R", "R", "R", "C"], total=total,
                       colors_=col, font_size=8.3))
    story.append(Spacer(1, 4 * mm))
    alacak = sum(c["bakiye"] for c in rows if c["bakiye"] > 0)
    borc = -sum(c["bakiye"] for c in rows if c["bakiye"] < 0)
    story.append(_kv_table([("Alacaklarımız (borçlu cariler)", _m(alacak)),
                            ("Borçlarımız (alacaklı cariler)", _m(borc))]))
    return _build(path, story, firma, kullanici, "Cari Bakiye Listesi")


# --------------------------------------------------------------------------- kullanım kılavuzu
def kilavuz_pdf(path, firma: dict, kullanici: str = "") -> Path:
    """Yazdırılabilir kısa kullanım kılavuzu."""
    st = _st()
    reg, bold = _fonts()
    story = _header(firma, "Hızlı Başlangıç Kılavuzu", ["Kasa Takip", f"Sürüm {__version__}"])

    def para(text):
        return Paragraph(text, st["metin"])

    def steps(items):
        rows = [[f"{i}.", Paragraph(t, st["metin"])] for i, t in enumerate(items, 1)]
        t = Table(rows, colWidths=[8 * mm, CONTENT_W - 8 * mm])
        t.setStyle(TableStyle([("FONT", (0, 0), (0, -1), bold, 10), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                               ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
        return t

    story += [_section("Her gün"), Spacer(1, 2 * mm), steps([
        "Programı açın, kullanıcı adı ve şifrenizle giriş yapın. Sol menüden <b>Günlük Kasa</b>'yı seçin "
        "(tarih otomatik olarak bugündür).",
        "Kayıtları üstteki <b>Hızlı kayıt</b> satırından girin: <b>Gelir/Gider</b> → <b>Kategori</b> → "
        "<b>Ödeme tipi</b> → <b>Tutar</b> → (varsa) <b>Cari</b> → <b>Açıklama</b> → <b>Enter</b>.",
        "Nakdi bankaya yatırdığınızda veya POS tutarı bankaya geçtiğinde <b>Virman</b> kaydı girin "
        "(bu gelir/gider sayılmaz, yalnızca bakiyeyi bir ödeme tipinden diğerine taşır).",
        "Gün sonunda kasayı sayın; <b>Günü Kapat</b>'a basın, sayılan tutarı yazın. Program farkı hesaplar, "
        "yedek alır ve <b>Günlük Kasa Raporu</b>'nu (PDF) açar; yazdırıp imzalayabilirsiniz.",
        "Kapanan gün kilitlenir. Hata fark ederseniz yöneticinin <b>Günü Aç</b> ile kapanışı geri alması gerekir.",
    ]), Spacer(1, 4 * mm)]

    story += [_section("Kayıt türleri"), Spacer(1, 1 * mm), _grid(
        ["Tür", "Kasa bakiyesi", "Cari hesap", "Örnek"],
        [["Gelir", "Artar", _p("Cari seçilirse alacak (tahsilat)"), _p("Gişe tahsilatı, müşteriden havale")],
         ["Gider", "Azalır", _p("Cari seçilirse borç (ödeme)"), _p("Yakıt, kira, tedarikçiye ödeme")],
         ["Virman", _p("Bir ödeme tipinden diğerine"), "—", _p("Nakit bankaya yatırıldı")],
         ["Cari Borç", "Etkilemez", "Borç", _p("Müşteriye veresiye gönderim")],
         ["Cari Alacak", "Etkilemez", "Alacak", _p("Tedarikçinin veresiye faturası")]],
        _w(18, 26, 30, 44), ["L", "L", "L", "L"], font_size=9),
        Spacer(1, 1.5 * mm),
        para("Cari bakiyesi: <b>(B)</b> cari bize borçlu, <b>(A)</b> biz cariye borçluyuz. Her ödeme tipinin "
             "(Nakit, Kart, Havale, Çek) bakiyesi ayrı hesaplanır ve ertesi güne devreder."),
        Spacer(1, 4 * mm)]

    story += [_section("Kısayollar"), Spacer(1, 1 * mm), _grid(
        ["Tuş", "İşlem", "Tuş", "İşlem"],
        [["F2", "Yeni gelir", "F3", "Yeni gider"], ["F4", "Virman", "F5", "Ekranı yenile"],
         ["Enter", "Seçili kaydı aç", "Delete", "Seçili kaydı sil"],
         ["Ctrl+C", "Seçili satırları kopyala", "Ctrl+V", "Excel'den yapıştır"],
         ["Ctrl+Enter", "Kaydet ve yeni kayıt", "Ctrl+P", "Günlük kasa raporu"],
         ["Ctrl+← / →", "Önceki / sonraki gün", "F1", "Yardım"]],
        _w(18, 32, 18, 32), ["L", "L", "L", "L"], font_size=9), Spacer(1, 4 * mm)]

    story += [KeepTogether([_section("Cariler"), Spacer(1, 2 * mm), steps([
        "Sol menüden <b>Cariler</b>; listeden cariyi seçin. Sağda bakiyesi ve ekstresi görünür.",
        "<b>Tahsilat Al</b>: cariden gelen para (kasaya gelir). <b>Ödeme Yap</b>: cariye ödenen para (kasadan gider).",
        "<b>Borç Fişi</b> / <b>Alacak Fişi</b>: kasayı etkilemeyen veresiye işlemler.",
        "<b>Ekstre PDF</b> ile dönem ekstresini alıp cariye gönderebilirsiniz.",
    ])]), Spacer(1, 4 * mm)]

    story += [KeepTogether([_section("Excel'den yapıştırma"), Spacer(1, 2 * mm), steps([
        "Excel'de satırları seçip <b>Ctrl+C</b> yapın.",
        "<b>Günlük Kasa</b> veya <b>Hareketler</b> ekranında <b>Excel'den Yapıştır</b>'a basın (ya da Ctrl+V).",
        "Program sütunları tanır. Yeşil satırlar kaydedilir, kırmızı satırların sebebi yazar. "
        "<b>Kaydı Ekle</b> ile bitirin.",
    ])]), Spacer(1, 4 * mm)]

    story += [KeepTogether([_section("Analiz ve raporlar"), Spacer(1, 2 * mm), steps([
        "<b>Analiz</b> ekranında dönemi ve karşılaştırmayı seçin. <b>Genel Bakış</b> göstergeleri, grafikleri ve "
        "otomatik yorumları; diğer sekmeler trend, kategori, kasa, cari yaşlandırma, gün/saat ve yıllık "
        "karşılaştırmayı gösterir. <b>PDF Rapor</b> ile dönem analiz raporu alınır.",
        "<b>Raporlar</b> ekranında listeden raporu seçin (ör. <b>Kasa Defteri</b>, <b>Cari Mizan</b>, "
        "<b>Alacak Yaşlandırma</b>), parametreleri ayarlayın; rapor ekranda önizlenir.",
        "<b>PDF Oluştur</b> ile yazdırılabilir PDF, <b>Excel'e Aktar</b> ile Excel dosyası alınır. Tablolarda satıra "
        "çift tıklayınca ilgili gün, cari veya kayıt açılır.",
        "Tüm defteri tek seferde Excel'e almak için: <b>Yedekleme → Tüm Verileri Excel'e Aktar</b> (bütün kayıtlar "
        "tek dosyada) veya <b>Raporlar → Kasa Defteri</b>, dönem <b>Tüm Zamanlar</b>, <b>Excel'e Aktar</b> "
        "(ilk kayıttan bugüne yürüyen bakiyeli kasa defteri).",
    ])]), Spacer(1, 4 * mm)]

    story += [KeepTogether([_section("Yedekler ve güvenlik"), Spacer(1, 2 * mm), steps([
        "Program açılışta, kapanışta ve her gün kapanışında kendiliğinden yedek alır "
        "(Belgeler\\KasaTakip\\Yedekler).",
        "<b>Yedekleme</b> ekranından bir USB bellek veya bulut klasörü <b>ek kopya klasörü</b> olarak seçilirse "
        "her yedek oraya da kopyalanır (önerilir).",
        "Yönetici şifresi unutulursa giriş ekranındaki <b>Şifremi unuttum</b> ile kurulumda verilen "
        "<b>kurtarma anahtarı</b> kullanılır. Diğer kullanıcıların şifresini yönetici sıfırlar.",
    ])])]
    return _build(path, story, firma, kullanici, "Kasa Takip Hızlı Başlangıç Kılavuzu")


# =========================================================================== genel rapor çizici
# Raporlar bölümündeki tüm raporlar (report_model blokları) bu çizici ile PDF'e dönüştürülür.
from .report_model import KV as RKV  # noqa: E402
from .report_model import OTHER_COLOR as R_OTHER  # noqa: E402
from .report_model import SERIES_COLORS as R_COLORS  # noqa: E402
from .report_model import Chart as RChart  # noqa: E402
from .report_model import Row as RRow  # noqa: E402
from .report_model import Table as RTable  # noqa: E402
from .report_model import Text as RText  # noqa: E402
from .report_model import align_of, format_cell, is_negative, unwrap  # noqa: E402

_TONE_COLOR = {"GELIR": GREEN, "GIDER": RED, "VIRMAN": AMBER, "CARI": PURPLE, "muted": MUTED}
_TEXT_TONE = {"good": "#166534", "bad": "#9F1239", "warn": "#92400E", "info": "#1F2A44"}
ROW_STACK_LIMIT = 25           # yan yana tablolarda en fazla satır (fazlası alt alta basılır)


def render_report(path, rep, firma: dict, kullanici: str = "") -> Path:
    pagesize = landscape(A4) if rep.landscape else A4
    width = pagesize[0] - 2 * MARGIN
    story = _header(firma, rep.title, rep.lines, width)
    for b in rep.blocks:
        story += _flow_block(b, width)
    if rep.signatures:
        story.append(_signatures(rep.signatures, width))
    return _build(path, story, firma, kullanici, rep.title, pagesize)


def _flow_block(b, width: float) -> list:
    if isinstance(b, RRow):
        return _flow_row(b, width)
    inner = _inner(b, width)
    if isinstance(b, RTable) and len(b.rows) > 12:
        return _long_section(b.title, width) + inner + [Spacer(1, 4 * mm)]
    return [KeepTogether([_section(b.title, width), Spacer(1, 1.6 * mm)] + inner), Spacer(1, 4 * mm)]


def _flow_row(row, width: float) -> list:
    blocks = [b for b in row.blocks if b is not None]
    if not blocks:
        return []
    too_long = any(isinstance(b, RTable) and len(b.rows) > ROW_STACK_LIMIT for b in blocks)
    if len(blocks) == 1 or too_long or any(isinstance(b, (RKV, RText)) for b in blocks):
        out = []
        for b in blocks:
            out += _flow_block(b, width)
        return out
    gap = 6 * mm
    w = (width - gap * (len(blocks) - 1)) / len(blocks)
    cells, widths = [], []
    for i, b in enumerate(blocks):
        if i:
            cells.append("")
            widths.append(gap)
        content = [[_section(b.title, w)], [Spacer(1, 1.6 * mm)]] + [[x] for x in _inner(b, w)]
        t = Table(content, colWidths=[w])
        t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        cells.append(t)
        widths.append(w)
    outer = Table([cells], colWidths=widths)
    outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return [outer, Spacer(1, 4 * mm)]


def _inner(b, width: float) -> list:
    st = _st()
    if isinstance(b, RKV):
        cols = 2 if width < 600 else 3
        reg, bold = _fonts()
        lab_st = ParagraphStyle("kvl", fontName=reg, fontSize=9.2, leading=11, textColor=colors.HexColor("#374151"))
        val_st = ParagraphStyle("kvv", fontName=bold, fontSize=9.6, leading=11.5, alignment=TA_RIGHT)
        cap_l = int(width / cols * 0.58 / 4.6)            # yaklaşık sığan karakter sayısı
        cap_v = int(width / cols * 0.42 / 5.0)
        pairs, vcol = [], {}
        for i, (lab, val, kind) in enumerate(b.items):
            txt = _tl(format_cell(val, kind)) if val is not None else "-"
            txt = txt or "-"
            lab_c = Paragraph(escape(str(lab)), lab_st) if len(str(lab)) > cap_l else lab
            val_c = Paragraph(escape(txt), val_st) if len(txt) > cap_v else txt
            pairs.append((lab_c, val_c))
            if is_negative(val, kind):
                vcol[i] = RED
        return [_kv_table(pairs, cols=cols, width=width, value_colors=vcol)]
    if isinstance(b, RText):
        out = []
        for tone, text in b.items:
            c = _TEXT_TONE.get(tone, _TEXT_TONE["info"])
            out.append(Paragraph(f'<font color="{c}">●</font>&nbsp;&nbsp;{escape(_tl(text))}', st["metin"]))
            out.append(Spacer(1, 0.8 * mm))
        return out or [Paragraph("—", st["bos"])]
    if isinstance(b, RChart):
        return [_chart_drawing(b, width)]
    if isinstance(b, RTable):
        if not b.rows:
            return [Spacer(1, 1 * mm), Paragraph(escape(b.empty), st["bos"])]
        out = _table_flow(b, width)
        if b.note:
            out += [Spacer(1, 1.2 * mm), Paragraph(escape(b.note), st["hucre_k"])]
        return out
    return []


def _tl(text) -> str:
    """PDF yazı tiplerinde ₺ simgesi olmayabilir; metinlerde TL yazılır."""
    return str(text).replace("₺", "TL")


def _col_widths(b, width: float) -> list[float]:
    if b.widths and len(b.widths) == len(b.headers):
        tot = sum(b.widths)
        return [width * w / tot for w in b.widths]
    est = []
    sample = b.rows[:300]
    for c, (h, kind) in enumerate(zip(b.headers, b.types)):
        lens = [len(format_cell(r[c], kind)) for r in sample if c < len(r)]
        if b.total and c < len(b.total):
            lens.append(len(format_cell(b.total[c], kind)))
        n = max([len(h) * 0.9] + lens)
        n = min(n, 50) if kind == "text" else min(n, 18)
        est.append(max(n, 4) + 2)
    tot = sum(est)
    return [width * x / tot for x in est]


TABLE_CHUNK = 300              # uzun tablolar bu kadar satırlık parçalara bölünür (reportlab'da hız için)
MAX_CELL_CHARS = 1500          # PDF'te bir hücrede gösterilecek en fazla karakter


def _table_flow(b, width: float) -> list:
    reg, bold = _fonts()
    fs = b.font
    widths = _col_widths(b, width)
    aligns = [align_of(k) for k in b.types]
    amap = {"L": TA_LEFT, "R": TA_RIGHT, "C": TA_CENTER}
    header = [Paragraph(escape(str(h)), ParagraphStyle(f"th{i}", fontName=bold, fontSize=fs, leading=fs * 1.18,
                                                       alignment=amap[aligns[i]]))
              for i, h in enumerate(b.headers)]
    cstyle = ParagraphStyle("td", fontName=reg, fontSize=fs, leading=fs * 1.22)
    caps = [max(3, int((w - 8) / (fs * 0.5))) for w in widths]
    rows, cols_, bold_rows, bg_rows = [], [], [], []
    n = len(b.headers)
    for i, row in enumerate(b.rows, 1):
        out = []
        for c in range(n):
            v = row[c] if c < len(row) else None
            kind = b.types[c]
            k = unwrap(v)[1] or kind
            txt = _tl(format_cell(v, kind)) if k == "text" else format_cell(v, kind)
            if len(txt) > MAX_CELL_CHARS:
                txt = txt[:MAX_CELL_CHARS] + " … (kısaltıldı)"
            out.append(Paragraph(escape(txt), cstyle) if k == "text" and len(txt) > caps[c] else txt)
            if is_negative(v, kind):
                cols_.append((c, i, RED))
        tone = b.tones[i - 1] if b.tones and i - 1 < len(b.tones) else None
        if tone in _TONE_COLOR:
            for c in (b.tone_cols if b.tone_cols is not None else range(n)):
                cols_.append((c, i, _TONE_COLOR[tone]))
        elif tone == "bold":
            bold_rows.append(i)
        elif tone == "sub":
            bold_rows.append(i)
            bg_rows.append(i)
        rows.append(out)
    total = None
    if b.total:
        total = [format_cell(v, k) for v, k in zip(b.total, b.types)]
    out = []
    for start in range(0, len(rows), TABLE_CHUNK):
        part = rows[start:start + TABLE_CHUNK]
        last = start + TABLE_CHUNK >= len(rows)
        sel = (lambda lst: [(r - start) for r in lst if start < r <= start + len(part)])
        pc = [(c, r - start, col) for (c, r, col) in cols_ if start < r <= start + len(part)]
        if last and total:
            for c, (v, k) in enumerate(zip(b.total, b.types)):
                if is_negative(v, k):
                    pc.append((c, len(part) + 1, RED))
        out.append(_grid(header, part, widths, aligns, total=total if last else None, font_size=fs, colors_=pc,
                         bold_rows=sel(bold_rows), bg_rows=sel(bg_rows), split_in_row=True))
    return out


# --------------------------------------------------------------------------- grafikler
def _elide(text: str, font: str, size: float, max_w: float) -> str:
    text = str(text)
    if pdfmetrics.stringWidth(text, font, size) <= max_w:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > max_w:
        text = text[:-1]
    return text + "…"


def _axis(vmin: float, vmax: float, ticks: int = 4):
    import math
    vmin, vmax = min(vmin, 0), max(vmax, 0)
    if vmin == vmax:
        vmax = 1
    raw = (vmax - vmin) / ticks
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag), 10 * mag)
    return math.floor(vmin / step) * step, math.ceil(vmax / step) * step, step


LEGEND_ROW = 11


def _legend_layout(series, x0: float, width: float, line: bool = False) -> list:
    """Lejant öğelerinin (x, satır) konumları; sığmayan öğe alt satıra geçer."""
    reg, _ = _fonts()
    out, x, row = [], x0, 0
    for name, color, _ in series:
        name = _elide(str(name), reg, 7.8, max(40.0, width - x0 - 30))
        w = (16 if line else 12) + pdfmetrics.stringWidth(name, reg, 7.8) + 14
        if x + w > width and x > x0:
            x, row = x0, row + 1
        out.append((x, row, name, color))
        x += w
    return out


def _legend_rows(series, x0: float, width: float, line: bool = False) -> int:
    lay = _legend_layout(series, x0, width, line)
    return (max(r for _, r, _, _ in lay) + 1) if lay else 0


def _legend(d: Drawing, series, x: float, y: float, line: bool = False) -> None:
    reg, _ = _fonts()
    for lx, row, name, color in _legend_layout(series, x, d.width, line):
        c = colors.HexColor(color) if isinstance(color, str) else NAVY
        ly = y - row * LEGEND_ROW
        if line:
            from reportlab.graphics.shapes import Line
            d.add(Line(lx, ly + 3.5, lx + 12, ly + 3.5, strokeColor=c, strokeWidth=1.8))
            tx = lx + 16
        else:
            d.add(Rect(lx, ly, 8, 8, fillColor=c, strokeColor=None))
            tx = lx + 12
        d.add(String(tx, ly + 1, name, fontName=reg, fontSize=7.8))


def _chart_drawing(ch, width: float) -> Drawing:
    if ch.kind == "donut":
        return _donut_drawing(ch, width, ch.height * mm)
    if ch.kind == "hbar":
        return _hbar_drawing(ch, width)
    if ch.kind == "line":
        return _line_drawing(ch, width, ch.height * mm)
    return _vbar_drawing(ch, width, ch.height * mm)


def _scaled(ch, vals):
    return [((v or 0) / 100 if ch.money else (v or 0)) for v in vals]


def _value_axis(axis, lo, hi, step, money: bool) -> None:
    reg, _ = _fonts()
    axis.valueMin, axis.valueMax, axis.valueStep = lo, hi, step
    axis.labelTextFormat = (lambda v: fmt_compact(int(round(v * 100)))) if money else (lambda v: f"{int(v)}")
    axis.labels.fontName = reg
    axis.labels.fontSize = 7
    axis.visibleGrid = True
    axis.gridStrokeColor = GRID
    axis.gridStrokeWidth = 0.4
    axis.strokeColor = GRID


def _category_names(labels: list[str], plot_w: float, min_w: float = 30) -> list[str]:
    n = len(labels)
    step = max(1, -(-n * min_w // max(plot_w, 1)))
    return [lab if i % int(step) == 0 else "" for i, lab in enumerate(labels)] or [""]


def _vbar_drawing(ch, width: float, height: float) -> Drawing:
    reg, _ = _fonts()
    d = Drawing(width, height)
    data = [_scaled(ch, vals) for _, _, vals in ch.series] or [[0]]
    n = max(1, len(ch.labels))
    if ch.kind == "stacked":
        pos = [sum(max(0, row[i]) for row in data if i < len(row)) for i in range(n)]
        neg = [sum(min(0, row[i]) for row in data if i < len(row)) for i in range(n)]
        lo, hi, step = _axis(min(neg + [0]), max(pos + [0]))
    else:
        allv = [x for row in data for x in row]
        lo, hi, step = _axis(min(allv + [0]), max(allv + [0]))
    fmt = (lambda v: fmt_compact(int(round(v * 100)))) if ch.money else (lambda v: f"{int(v)}")
    axis_w = max(pdfmetrics.stringWidth(fmt(lo), reg, 7), pdfmetrics.stringWidth(fmt(hi), reg, 7)) + 10
    extra = (_legend_rows(ch.series, axis_w, width) - 1) * LEGEND_ROW
    if extra > 0:
        height += extra
        d.height = height
    bc = VerticalBarChart()
    bc.x, bc.y = axis_w, 22
    bc.width, bc.height = width - axis_w - 6, height - 22 - 16 - max(0, extra)
    bc.data = data
    for i, (_, color, _) in enumerate(ch.series):
        bc.bars[i].fillColor = colors.HexColor(color if isinstance(color, str) else R_COLORS[i % len(R_COLORS)])
    bc.bars.strokeColor = None
    _value_axis(bc.valueAxis, lo, hi, step, ch.money)
    if ch.kind == "stacked":
        bc.categoryAxis.style = "stacked"
    bc.categoryAxis.categoryNames = _category_names(list(ch.labels), bc.width)
    bc.categoryAxis.labels.fontName = reg
    bc.categoryAxis.labels.fontSize = 6.8
    bc.categoryAxis.labels.dy = -2
    bc.categoryAxis.strokeColor = GRID
    bc.categoryAxis.visibleTicks = False
    bc.categoryAxis.joinAxisMode = "bottom"
    bc.groupSpacing = 2 if n > 20 else 5
    bc.barSpacing = 0.5
    d.add(bc)
    _legend(d, ch.series, axis_w, height - 9)
    return d


def _line_drawing(ch, width: float, height: float) -> Drawing:
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.widgets.markers import makeMarker
    reg, _ = _fonts()
    d = Drawing(width, height)
    n = max(1, len(ch.labels))
    series = [s_ for s_ in ch.series if any(v is not None for v in s_[2])]
    if not series:
        d.add(String(4, height / 2, "Kayıt yok", fontName=reg, fontSize=9, fillColor=MUTED))
        return d
    ch = RChart(ch.title, ch.kind, ch.labels, series, ch.money, ch.height, ch.titles)
    data = [[((v / 100 if ch.money else v) if v is not None else None) for v in vals] for _, _, vals in ch.series]
    allv = [x for row in data for x in row if x is not None] or [0]
    lo, hi, step = _axis(min(allv + [0]), max(allv + [0]))
    fmt = (lambda v: fmt_compact(int(round(v * 100)))) if ch.money else (lambda v: f"{int(v)}")
    axis_w = max(pdfmetrics.stringWidth(fmt(lo), reg, 7), pdfmetrics.stringWidth(fmt(hi), reg, 7)) + 10
    extra = (_legend_rows(ch.series, axis_w, width, line=True) - 1) * LEGEND_ROW
    if extra > 0:
        height += extra
        d.height = height
    lc = HorizontalLineChart()
    lc.x, lc.y = axis_w, 22
    lc.width, lc.height = width - axis_w - 6, height - 22 - 16 - max(0, extra)
    lc.data = data or [[0]]
    lc.joinedLines = 1
    for i, (_, color, _) in enumerate(ch.series):
        lc.lines[i].strokeColor = colors.HexColor(color if isinstance(color, str) else R_COLORS[i % len(R_COLORS)])
        lc.lines[i].strokeWidth = 1.6
        if n <= 31:
            m = makeMarker("FilledCircle")
            m.size = 2.6
            m.fillColor = lc.lines[i].strokeColor
            lc.lines[i].symbol = m
    _value_axis(lc.valueAxis, lo, hi, step, ch.money)
    lc.categoryAxis.categoryNames = _category_names(list(ch.labels), lc.width)
    lc.categoryAxis.labels.fontName = reg
    lc.categoryAxis.labels.fontSize = 6.8
    lc.categoryAxis.labels.dy = -2
    lc.categoryAxis.strokeColor = GRID
    lc.categoryAxis.visibleTicks = False
    lc.categoryAxis.joinAxisMode = "bottom"
    d.add(lc)
    _legend(d, ch.series, axis_w, height - 9, line=True)
    return d


def _donut_drawing(ch, width: float, height: float) -> Drawing:
    from reportlab.graphics.charts.piecharts import Pie
    reg, bold = _fonts()
    width = min(width, 150 * mm)
    d = Drawing(width, height)
    d.hAlign = "LEFT"
    vals = list(ch.series[0][2]) if ch.series else []
    items = sorted([(lab, v) for lab, v in zip(ch.labels, vals) if v and v > 0], key=lambda x: x[1], reverse=True)
    if not items:
        d.add(String(4, height / 2, "Kayıt yok", fontName=reg, fontSize=9, fillColor=MUTED))
        return d
    if len(items) > 8:
        rest = sum(v for _, v in items[7:])
        items = items[:7] + [(f"Diğer ({len(items) - 7} kalem)", rest)]
    given = ch.series[0][1] if isinstance(ch.series[0][1], list) else None
    cols_ = [(given[i] if given and i < len(given) else
              (R_OTHER if lab.startswith("Diğer (") else R_COLORS[i % len(R_COLORS)])) for i, (lab, _) in
             enumerate(items)]
    total = sum(v for _, v in items)
    size = min(height - 6, width * 0.42)
    pie = Pie()
    pie.x, pie.y = 4, (height - size) / 2
    pie.width = pie.height = size
    pie.data = [v for _, v in items]
    pie.labels = None
    pie.innerRadiusFraction = 0.56
    pie.startAngle = 90
    pie.direction = "clockwise"
    pie.slices.strokeColor = colors.white
    pie.slices.strokeWidth = 0.8
    for i, c in enumerate(cols_):
        pie.slices[i].fillColor = colors.HexColor(c)
    d.add(pie)
    cx, cy = pie.x + size / 2, pie.y + size / 2
    d.add(String(cx, cy + 1, fmt_compact(total) if ch.money else str(total), fontName=bold, fontSize=9,
                 textAnchor="middle"))
    d.add(String(cx, cy - 9, "Toplam", fontName=reg, fontSize=6.8, textAnchor="middle", fillColor=MUTED))
    lx = pie.x + size + 12
    lw = width - lx
    rh = min(11.5, (height - 4) / len(items))
    top = cy + rh * len(items) / 2 - rh
    val_w = 64 if ch.money else 30
    pct_w = 30
    show_val = lw > val_w + pct_w + 70
    for i, ((lab, v), c) in enumerate(zip(items, cols_)):
        y = top - i * rh
        d.add(Rect(lx, y + 1.5, 7, 7, fillColor=colors.HexColor(c), strokeColor=None))
        label_w = lw - 12 - pct_w - (val_w if show_val else 0)
        d.add(String(lx + 11, y + 2, _elide(lab, reg, 7.6, label_w), fontName=reg, fontSize=7.6))
        d.add(String(lx + 11 + label_w + pct_w - 2, y + 2, fmt_percent(v / total), fontName=reg, fontSize=7.6,
                     textAnchor="end", fillColor=MUTED))
        if show_val:
            d.add(String(width - 2, y + 2, fmt_money(v) if ch.money else str(v), fontName=reg, fontSize=7.6,
                         textAnchor="end"))
    return d


def _hbar_drawing(ch, width: float) -> Drawing:
    reg, bold = _fonts()
    vals = list(ch.series[0][2]) if ch.series else []
    given = ch.series[0][1] if ch.series and isinstance(ch.series[0][1], list) else None
    rows = list(zip(ch.labels, vals))
    rh = 14
    height = max(1, len(rows)) * rh + 4
    d = Drawing(width, height)
    vmax = max([v or 0 for _, v in rows] + [1])
    total = sum(v or 0 for _, v in rows) or 1
    label_w, val_w, pct_w = width * 0.22, 74, 36
    bar_x = label_w + 6
    bar_w = max(30, width - bar_x - val_w - pct_w - 8)
    for i, (lab, v) in enumerate(rows):
        y = height - (i + 1) * rh
        c = colors.HexColor(given[i] if given and i < len(given) else R_COLORS[i % len(R_COLORS)])
        d.add(String(0, y + 4, _elide(lab, reg, 8, label_w), fontName=reg, fontSize=8))
        d.add(Rect(bar_x, y + 3, bar_w, 7, fillColor=colors.HexColor("#EEF1F6"), strokeColor=None))
        if v:
            d.add(Rect(bar_x, y + 3, max(2, bar_w * v / vmax), 7, fillColor=c, strokeColor=None))
        d.add(String(bar_x + bar_w + pct_w, y + 4, fmt_percent((v or 0) / total), fontName=reg, fontSize=7.8,
                     textAnchor="end", fillColor=MUTED))
        d.add(String(width - 2, y + 4, fmt_money(v or 0) if ch.money else str(v or 0), fontName=bold, fontSize=8,
                     textAnchor="end"))
    return d
