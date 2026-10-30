"""Rapor içerik modeli.

Bir rapor; ekranda önizlenen, PDF'e ve Excel'e aynı içerikle aktarılan bloklardan oluşur:
    KV      özet göstergeler (etiket / değer / tür)
    Table   tablo
    Chart   grafik (bar, stacked, line, donut, hbar)
    Text    madde madde yorumlar veya açıklamalar
    Row     yan yana yerleşen bloklar

Hücre / değer türleri:
    text, money (tutar), smoney (işaretli tutar, eksi kırmızı), dmoney (fark: +/−), balance (cari bakiye B/A),
    date, datetime, int, percent (oran), dpercent (değişim oranı +/−), dpp (puan farkı), num
Tablo hücresinde sütun türünden farklı bir biçim gerekiyorsa V(değer, tür) kullanılır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .util import fmt_balance, fmt_date, fmt_delta_percent, fmt_dt, fmt_money, fmt_percent

SERIES_COLORS = ["#2563EB", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#06B6D4", "#EC4899", "#84CC16",
                 "#F97316", "#14B8A6"]
OTHER_COLOR = "#A3AEC2"
NUMERIC_KINDS = ("money", "smoney", "dmoney", "balance", "int", "percent", "dpercent", "dpp", "num")
MONEY_KINDS = ("money", "smoney", "dmoney", "balance")


@dataclass
class V:
    value: Any
    kind: str


@dataclass
class KV:
    title: str
    items: list                          # [(etiket, değer, tür)]
    cols: int = 4


@dataclass
class Table:
    title: str
    headers: list
    types: list
    rows: list
    total: list | None = None
    widths: list | None = None           # PDF için oransal sütun genişlikleri
    note: str = ""
    sheet: str = ""                      # Excel sayfa adı (boşsa başlık)
    tones: list | None = None            # satır tonu: GELIR|GIDER|VIRMAN|CARI|bold|sub|muted|None
    tone_cols: list | None = None        # tonun renklendirdiği sütunlar (None: tümü)
    keys: list | None = None             # satır anahtarları (ekranda çift tıklayınca açmak için)
    key_kind: str = ""                   # 'hareket' | 'cari' | 'gun'
    font: float = 8.4
    empty: str = "Bu dönemde kayıt yok."
    max_preview: int = 0                 # önizlemede en fazla satır (0: sınırsız)


@dataclass
class Chart:
    title: str
    kind: str                            # bar | stacked | line | donut | hbar
    labels: list
    series: list                         # [(ad, renk, değerler)]
    money: bool = True
    height: float = 62.0                 # PDF yüksekliği (mm)
    titles: list | None = None           # ekrandaki ipucu başlıkları


@dataclass
class Text:
    title: str
    items: list                          # [(ton, metin)]  ton: good|bad|warn|info


@dataclass
class Row:
    blocks: list


@dataclass
class Report:
    key: str
    title: str
    lines: list
    blocks: list
    filename: str
    subdir: str = "Raporlar"
    landscape: bool = False
    signatures: list | None = None
    meta: dict = field(default_factory=dict)


def unwrap(v):
    """(değer, tür|None)"""
    if isinstance(v, V):
        return v.value, v.kind
    return v, None


def _sign(v) -> str:
    return "+" if v > 0 else ("−" if v < 0 else "")


def fmt_int(v) -> str:
    return f"{int(v):,}".replace(",", ".")


def format_cell(v, kind: str = "text") -> str:
    raw, k = unwrap(v)
    kind = k or kind
    if raw is None or raw == "":
        return ""
    try:
        if kind in ("money", "smoney"):
            return fmt_money(raw)
        if kind == "dmoney":
            return fmt_money(raw, plus=True)
        if kind == "balance":
            return fmt_balance(raw)
        if kind == "date":
            return fmt_date(raw)
        if kind == "datetime":
            return fmt_dt(raw)
        if kind == "int":
            return fmt_int(raw)
        if kind == "percent":
            return fmt_percent(raw)
        if kind == "dpercent":
            return fmt_delta_percent(raw)
        if kind == "dpp":
            return f"{_sign(raw)}{abs(raw) * 100:.1f} puan".replace(".", ",")
        if kind == "num":
            return f"{raw:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") \
                if isinstance(raw, float) else fmt_int(raw)
    except (TypeError, ValueError):
        return str(raw)
    return str(raw)


def is_negative(v, kind: str) -> bool:
    raw, k = unwrap(v)
    kind = k or kind
    return kind in ("smoney", "dmoney", "dpercent", "dpp") and isinstance(raw, (int, float)) and raw < 0


def align_of(kind: str) -> str:
    if kind in NUMERIC_KINDS:
        return "R"
    if kind in ("date", "datetime"):
        return "C"
    return "L"


def flatten(blocks) -> list:
    out = []
    for b in blocks:
        if isinstance(b, Row):
            out += flatten(b.blocks)
        else:
            out.append(b)
    return out
