"""Para, tarih ve Türkçe metin için biçimlendirme/ayrıştırma yardımcıları.

Tutarlar veritabanında kuruş cinsinden tam sayı (int) olarak tutulur;
böylece yuvarlama hatası oluşmaz. 1.250,50 TL -> 125050
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
             "Eylül", "Ekim", "Kasım", "Aralık"]
TR_MONTHS_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
TR_DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
TR_DAYS_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]

_FOLD_MAP = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
                           "â": "a", "î": "i", "û": "u"})


# --------------------------------------------------------------------------- metin
def tr_lower(s) -> str:
    return str(s if s is not None else "").replace("I", "ı").replace("İ", "i").lower()


def tr_upper(s) -> str:
    return str(s if s is not None else "").replace("i", "İ").replace("ı", "I").upper()


def fold(s) -> str:
    """Karşılaştırma anahtarı: küçük harf, Türkçe harfler sadeleştirilmiş, tek boşluk.
    'Havale/EFT Tahsilatı' -> 'havale/eft tahsilati'"""
    if s is None:
        return ""
    t = tr_lower(s).translate(_FOLD_MAP)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.split())


def loose(s) -> str:
    """Sadece harf/rakam bırakan daha gevşek karşılaştırma anahtarı."""
    return re.sub(r"[^0-9a-z]", "", fold(s))


def clean_text(s, limit: int | None = None) -> str:
    t = " ".join(str(s if s is not None else "").split())
    return t[:limit] if limit else t


# --------------------------------------------------------------------------- para
def fmt_money(kurus, symbol: bool = False, plus: bool = False) -> str:
    """125050 -> '1.250,50'  (symbol=True ise '1.250,50 ₺')"""
    if kurus is None or kurus == "":
        return ""
    k = int(kurus)
    sign = "-" if k < 0 else ("+" if plus and k > 0 else "")
    lira, kr = divmod(abs(k), 100)
    s = sign + f"{lira:,}".replace(",", ".") + f",{kr:02d}"
    return s + " ₺" if symbol else s


def fmt_balance(kurus, symbol: bool = False) -> str:
    """Cari bakiyesi: pozitif -> (B) borçlu, negatif -> (A) alacaklı."""
    k = int(kurus or 0)
    if k == 0:
        return fmt_money(0, symbol)
    return f"{fmt_money(abs(k), symbol)} ({'B' if k > 0 else 'A'})"


def balance_word(kurus) -> str:
    k = int(kurus or 0)
    return "Borçlu" if k > 0 else ("Alacaklı" if k < 0 else "Kapalı")


def fmt_compact(kurus) -> str:
    """Grafik eksenleri için kısa gösterim: 12.500 TL -> '12,5 bin'"""
    v = (kurus or 0) / 100
    a = abs(v)
    if a >= 1_000_000:
        s = f"{v / 1_000_000:.1f}".replace(".", ",") + " mn"
    elif a >= 1_000:
        s = f"{v / 1_000:.1f}".replace(".", ",") + " bin"
    else:
        s = f"{v:.0f}"
    return s.replace(",0 ", " ")


def fmt_percent(x, digits: int = 1) -> str:
    """0.125 -> '%12,5' ; -0.05 -> '-%5,0'"""
    if x is None:
        return "-"
    s = f"{abs(x) * 100:.{digits}f}"
    neg = x < 0 and float(s) != 0
    return ("-%" if neg else "%") + s.replace(".", ",")


def fmt_delta_percent(x, digits: int = 1) -> str:
    """Değişim oranı: 0.12 -> '+%12,0' ; -0.05 -> '-%5,0'"""
    if x is None:
        return ""
    s = f"{abs(x) * 100:.{digits}f}"
    if float(s) == 0:
        return "%" + s.replace(".", ",")
    return ("+%" if x > 0 else "-%") + s.replace(".", ",")


_RE_PLAIN = re.compile(r"\d+(?:[.,]\d{1,2})?")               # 1250 | 1250,5 | 1250.50
_RE_TR = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?")         # 1.250 | 1.250.000,50
_RE_EN = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?")         # 1,250.50 | 1,250,000
_RE_AMBIGUOUS = re.compile(r"\d{1,3},\d{3}")                    # 1,250 -> 1.250 mi 1,25 mi?
MAX_AMOUNT = 100_000_000_000                                    # kuruş (1 milyar TL)


def parse_amount(value):
    """Kullanıcının yazdığı veya Excel'den yapıştırdığı tutarı kuruşa çevirir.
    Kabul edilenler: 1.250,50 | 1250,50 | 1,250.50 | 1250.50 | 1.250 | 1.250.000 | ₺ / TL ekli | (125) | -125
    En fazla 2 ondalık hane; binlik gruplar 3 haneli olmalı. Belirsiz veya bozuk yazımlar
    (ör. '1,250', '1.000.50', '0,005') reddedilir ve None döner; böylece yanlış tutar sessizce kaydedilmez."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100
    if isinstance(value, (float, Decimal)):
        try:
            return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except InvalidOperation:
            return None
    s = str(value).strip()
    for token in ("₺", "TRY", "TL", "Tl", "tl", "\u00a0", "\u202f", " ", "'"):
        s = s.replace(token, "")
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s[:1] in ("-", "−"):
        neg, s = True, s[1:]
    elif s[:1] == "+":
        s = s[1:]
    if s.endswith("-") and not neg:
        neg, s = True, s[:-1]
    if _RE_PLAIN.fullmatch(s):
        num = s.replace(",", ".")
    elif _RE_TR.fullmatch(s):
        num = s.replace(".", "").replace(",", ".")
    elif _RE_EN.fullmatch(s) and not _RE_AMBIGUOUS.fullmatch(s):
        num = s.replace(",", "")
    else:
        return None
    try:
        d = Decimal(num)
    except InvalidOperation:
        return None
    k = int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return -k if neg else k


# --------------------------------------------------------------------------- tarih
_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d",
                 "%d.%m.%y", "%d/%m/%y", "%d-%m-%y")


def today() -> dt.date:
    return dt.date.today()


def now_iso() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_time() -> str:
    return dt.datetime.now().strftime("%H:%M")


def parse_date(value, default_year: int | None = None):
    """'28.10.2026', '28/10/26', '2026-10-28', '28 Ekim 2026', '28.10' gibi değerleri tarihe çevirir."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    if not s:
        return None
    m = re.match(r"^(\S+)\s+\d{1,2}:\d{2}(:\d{2})?$", s)
    if m:
        s = m.group(1)
    for f in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", s)
    if m:
        return _no_year(int(m.group(1)), int(m.group(2)), default_year)
    if re.fullmatch(r"\d{5}(\.0+)?", s):          # Excel seri numarası
        n = int(float(s))
        if 20000 < n < 80000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=n)
    m = re.fullmatch(r"(\d{1,2})\s+([^\W\d_]+)\.?\s*(\d{4})?", s)
    if m:
        mon = fold(m.group(2))[:3]
        months = [fold(x)[:3] for x in TR_MONTHS]
        if mon in months:
            if m.group(3):
                try:
                    return dt.date(int(m.group(3)), months.index(mon) + 1, int(m.group(1)))
                except ValueError:
                    return None
            return _no_year(int(m.group(1)), months.index(mon) + 1, default_year)
    return None


def _no_year(day: int, month: int, default_year: int | None):
    """Yılı yazılmamış tarih: verilen yıl, yoksa bugünü geçmeyen en yakın yıl."""
    year = default_year or today().year
    try:
        d = dt.date(year, month, day)
    except ValueError:
        return None
    if default_year is None and d > today():
        try:
            d = dt.date(year - 1, month, day)
        except ValueError:
            return None
    return d


MIN_YEAR = 2000


def date_in_range(d) -> bool:
    """Kayıt tarihi makul aralıkta mı (2000 - bugünden en fazla 1 yıl sonrası)?"""
    return d is not None and d.year >= MIN_YEAR and d <= today() + dt.timedelta(days=366)


def looks_like_date(value) -> bool:
    s = str(value or "").strip()
    if not s:
        return False
    if not (re.search(r"\d[./-]\d", s) or re.search(r"[^\W\d_]", s)):
        return False
    return parse_date(s) is not None


def parse_time(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.time):
        return value.strftime("%H:%M")
    s = str(value).strip()
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?::\d{2})?", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            return f"{h:02d}:{mi:02d}"
    return ""


def to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip()
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return parse_date(s)


def iso(v) -> str:
    d = to_date(v)
    return d.isoformat() if d else ""


def fmt_date(v, weekday: bool = False) -> str:
    d = to_date(v)
    if not d:
        return ""
    s = d.strftime("%d.%m.%Y")
    return f"{s} {TR_DAYS[d.weekday()]}" if weekday else s


def fmt_date_long(v) -> str:
    d = to_date(v)
    if not d:
        return ""
    return f"{d.day} {TR_MONTHS[d.month - 1]} {d.year} {TR_DAYS[d.weekday()]}"


def fmt_dt(v) -> str:
    """'2026-10-28 14:05:33' -> '28.10.2026 14:05'"""
    if not v:
        return ""
    s = str(v)
    d = to_date(s[:10])
    return f"{fmt_date(d)} {s[11:16]}".strip() if d else s


# --------------------------------------------------------------------------- dönemler
def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def month_end(d: dt.date) -> dt.date:
    nxt = d.replace(day=28) + dt.timedelta(days=4)
    return nxt - dt.timedelta(days=nxt.day)


def add_months(d: dt.date, n: int) -> dt.date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return dt.date(y, m, 1)


PERIOD_PRESETS = ["Bugün", "Dün", "Bu Hafta", "Geçen Hafta", "Bu Ay", "Geçen Ay", "Son 30 Gün",
                  "Son 3 Ay", "Bu Yıl", "Geçen Yıl", "Tüm Zamanlar"]
ALL_TIME_START = dt.date(2000, 1, 1)
ALL_TIME_END = dt.date(2099, 12, 31)


def period_range(name: str, ref: dt.date | None = None):
    t = ref or today()
    one = dt.timedelta(days=1)
    if name == "Bugün":
        return t, t
    if name == "Dün":
        return t - one, t - one
    if name == "Bu Hafta":
        return t - dt.timedelta(days=t.weekday()), t
    if name == "Geçen Hafta":
        s = t - dt.timedelta(days=t.weekday() + 7)
        return s, s + dt.timedelta(days=6)
    if name == "Bu Ay":
        return month_start(t), t
    if name == "Geçen Ay":
        s = add_months(t, -1)
        return s, month_end(s)
    if name == "Son 30 Gün":
        return t - dt.timedelta(days=29), t
    if name == "Son 3 Ay":
        return add_months(t, -2), t
    if name == "Bu Yıl":
        return dt.date(t.year, 1, 1), t
    if name == "Geçen Yıl":
        return dt.date(t.year - 1, 1, 1), dt.date(t.year - 1, 12, 31)
    if name == "Tüm Zamanlar":
        return ALL_TIME_START, ALL_TIME_END
    raise KeyError(name)


def previous_period(start: dt.date, end: dt.date):
    length = (end - start).days + 1
    pe = start - dt.timedelta(days=1)
    return pe - dt.timedelta(days=length - 1), pe


def auto_group(start: dt.date, end: dt.date) -> str:
    days = (end - start).days + 1
    if days <= 45:
        return "day"
    if days <= 200:
        return "week"
    return "month"


def group_key(d: dt.date, group: str) -> str:
    if group == "day":
        return d.isoformat()
    if group == "week":
        return (d - dt.timedelta(days=d.weekday())).isoformat()
    return d.strftime("%Y-%m")


def group_label(key: str, group: str) -> str:
    if group == "month":
        y, m = key.split("-")
        return f"{TR_MONTHS_SHORT[int(m) - 1]} {y[2:]}"
    d = dt.date.fromisoformat(key)
    return d.strftime("%d.%m")


def group_title(key: str, group: str) -> str:
    """Uzun etiket (ipucu ve tablolar için)."""
    if group == "month":
        y, m = key.split("-")
        return f"{TR_MONTHS[int(m) - 1]} {y}"
    d = dt.date.fromisoformat(key)
    if group == "week":
        return f"{fmt_date(d)} – {fmt_date(d + dt.timedelta(days=6))}"
    return fmt_date(d, weekday=True)


def iter_group_keys(start: dt.date, end: dt.date, group: str):
    seen = set()
    d = start
    while d <= end:
        k = group_key(d, group)
        if k not in seen:
            seen.add(k)
            yield k
        if group == "month":
            d = add_months(d, 1)
        else:
            d += dt.timedelta(days=1 if group == "day" else 7)
            if group == "week":
                d = d - dt.timedelta(days=d.weekday())
