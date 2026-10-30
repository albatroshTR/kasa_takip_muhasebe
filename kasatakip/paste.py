"""Excel'den (veya başka bir tablodan) kopyalanıp yapıştırılan satırların okunması.

Akış:  metin -> parse_table_text() -> sütun eşleştirme (başlıktan veya içerikten tahmin)
       -> hazirla_hareketler() / hazirla_cariler() -> her satır için 'geçerli/hatalı' sonucu
Kaydetme işini Service.import_movements / import_caris yapar.

İlke: emin olunamayan hiçbir değer tahminle kaydedilmez; satır kırmızı (hatalı) gösterilir.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from .util import clean_text, date_in_range, fmt_balance, fmt_date, fmt_money, fold, looks_like_date, loose, \
    parse_amount, parse_date, parse_time

_DASHES = {"-", "–", "—", "−"}


def _blank(v) -> bool:
    """Boş hücre veya Excel muhasebe biçimindeki sıfır işareti ('-')."""
    t = str(v if v is not None else "").strip()
    return not t or t in _DASHES


# --------------------------------------------------------------------------- metin <-> tablo
def _consistent(rows: list[list[str]]) -> bool:
    return len({len(r) for r in rows}) <= 1


def _unquote(cell: str) -> str:
    """Excel'in tırnakladığı hücre ("a ""b"" c") -> a "b" c"""
    if len(cell) >= 2 and cell[0] == '"' and cell[-1] == '"':
        inner = cell[1:-1]
        if '"' not in inner.replace('""', ""):
            return inner.replace('""', '"')
    return cell


def parse_table_text(text: str) -> list[list[str]]:
    """Excel'den kopyalanan metni satır/sütun tablosuna çevirir.
    Önce satır ve sekme bazında düz bölünür. Yalnızca düz bölme tutarsız sütun sayısı veriyorsa (hücre içinde
    satır sonu olan Excel tırnaklaması) tırnaklı ayrıştırma denenir. Böylece hücre başındaki tek bir '"'
    (ör. 'aynı' işareti) sonraki satırları yutmaz."""
    if not text or not text.strip():
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\t" in text:
        delim = "\t"
    else:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        delim = ";" if lines and all(";" in ln for ln in lines) else "\t"
    naive = [[c.strip() for c in ln.split(delim)] for ln in text.split("\n")]
    naive = [r for r in naive if any(r)]
    rows = [[_unquote(c) for c in r] for r in naive]
    if '"' in text and not _consistent(naive):
        quoted = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim, quotechar='"')]
        quoted = [r for r in quoted if any(r)]
        if quoted and _consistent(quoted):
            rows = quoted
    if not rows:
        return []
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def to_tsv(rows) -> str:
    def clean(v):
        return str(v if v is not None else "").replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return "\r\n".join("\t".join(clean(c) for c in r) for r in rows)


# --------------------------------------------------------------------------- bağlam
@dataclass
class Baglam:
    """Eşleştirme için mevcut tanımlar. Değerler: (id, ad, aktif)"""
    kategoriler: dict          # tur -> {anahtar: (id, ad, aktif)}
    odemeler: dict             # anahtar -> (id, ad, aktif)
    cariler: dict              # anahtar (ünvan ve kod) -> (id, ünvan, aktif)
    kilit: str | None = None   # son kapalı gün (ISO)

    @classmethod
    def from_service(cls, svc) -> "Baglam":
        kat = {"GELIR": {}, "GIDER": {}}
        for k in svc.categories():
            val = (k["id"], k["ad"], bool(k["aktif"]))
            kat[k["tur"]][fold(k["ad"])] = val
            kat[k["tur"]].setdefault(loose(k["ad"]), val)
        odm = {}
        for o in svc.payment_types():
            val = (o["id"], o["ad"], bool(o["aktif"]))
            odm[fold(o["ad"])] = val
            odm.setdefault(loose(o["ad"]), val)
        car = {}
        for c in svc.caris(include_inactive=True):
            val = (c["id"], c["unvan"], bool(c["aktif"]))
            car[fold(c["unvan"])] = val
            if c["kod"]:
                car.setdefault(fold(c["kod"]), val)
        return cls(kat, odm, car, svc.lock_date())


_ODEME_TAKMA = {
    "kredi karti": "kart", "banka karti": "kart", "kk": "kart", "pos": "kart", "kart": "kart",
    "eft": "havale", "havale/eft": "havale", "havale eft": "havale", "banka": "havale", "fast": "havale",
    "pesin": "nakit", "nakit": "nakit", "cek": "cek",
}


def match_kategori(text, ctx: Baglam, tur: str):
    if not text:
        return None
    m = ctx.kategoriler.get(tur, {})
    return m.get(fold(text)) or m.get(loose(text))


def match_odeme(text, ctx: Baglam):
    """Ödeme tipi adı eşleştirme. Birden fazla aday varsa (ör. 'Kart Ziraat' ve 'Kart Akbank') None döner."""
    k = fold(text)
    if not k:
        return None
    if k in ctx.odemeler:
        return ctx.odemeler[k]
    if loose(text) in ctx.odemeler:
        return ctx.odemeler[loose(text)]
    alias = _ODEME_TAKMA.get(k)
    if alias:
        cands = {v for key, v in ctx.odemeler.items() if key == alias or key.startswith(alias)}
        return next(iter(cands)) if len(cands) == 1 else None
    cands = {v for key, v in ctx.odemeler.items() if key.startswith(k)}
    return next(iter(cands)) if len(cands) == 1 else None


def match_cari(text, ctx: Baglam):
    k = fold(text)
    return ctx.cariler.get(k) if k else None


_TUR_GELIR = {"gelir", "giris", "gelen", "tahsilat", "g"}
_TUR_GIDER = {"gider", "cikis", "giden", "harcama", "odeme", "c"}


def tur_from_text(text):
    k = fold(text)
    if k in _TUR_GELIR:
        return "GELIR"
    if k in _TUR_GIDER:
        return "GIDER"
    return None


def _is_amount(v) -> bool:
    return not looks_like_date(v) and parse_amount(v) is not None


def _is_time(v) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", str(v).strip())) and parse_time(v) != ""


def detect_header(first_row: list[str], synonyms: dict) -> list[str] | None:
    rev = {}
    for f, words in synonyms.items():
        for w in words:
            rev[fold(w)] = f
    mapping, used = [], set()
    for cell in first_row:
        f = rev.get(fold(cell)) or rev.get(fold(re.sub(r"\(.*?\)", "", cell)))
        if f and f not in used:
            mapping.append(f)
            used.add(f)
        else:
            mapping.append("yok")
    filled = sum(1 for c in first_row if c.strip())
    return mapping if len(used) >= min(2, max(1, filled)) else None


def _values(sample, c):
    return [r[c] for r in sample if c < len(r) and not _blank(r[c])]


# --------------------------------------------------------------------------- satır sonucu
@dataclass
class HazirSatir:
    no: int
    hata: str = ""
    uyari: str = ""
    veri: dict = field(default_factory=dict)
    acilis: dict | None = None
    yeni_kategori: tuple | None = None
    yeni_cari: str | None = None
    ozet: str = ""

    @property
    def gecerli(self) -> bool:
        return not self.hata


class _Hata(Exception):
    pass


# --------------------------------------------------------------------------- hareketler
HAREKET_ALANLARI = [
    ("yok", "— Kullanma —"), ("tarih", "Tarih"), ("saat", "Saat"), ("tur", "Tür (Gelir/Gider)"),
    ("kategori", "Kategori"), ("odeme", "Ödeme Tipi"), ("tutar", "Tutar"), ("gelir_tutar", "Gelir Tutarı"),
    ("gider_tutar", "Gider Tutarı"), ("cari", "Cari"), ("aciklama", "Açıklama"), ("belge_no", "Belge No"),
]
HAREKET_ESANLAM = {
    "tarih": ["tarih", "date", "islem tarihi", "fis tarihi", "gun"],
    "saat": ["saat", "time"],
    "tur": ["tur", "tip", "islem turu", "islem tipi", "gelir/gider", "gelir gider", "hareket turu",
            "hareket tipi", "type"],
    "kategori": ["kategori", "gelir kategorisi", "gider kategorisi", "kalem", "category", "gelir turu",
                 "gider turu", "hesap adi"],
    "odeme": ["odeme tipi", "odeme sekli", "odeme turu", "odeme yontemi", "tahsilat sekli"],
    "tutar": ["tutar", "miktar", "bedel", "amount", "tutar (tl)", "tutar tl", "tutar(tl)"],
    "gelir_tutar": ["gelir", "giris", "gelen", "tahsilat", "gelir tutari", "giris tutari"],
    "gider_tutar": ["gider", "cikis", "giden", "harcama", "gider tutari", "cikis tutari", "odeme"],
    "cari": ["cari", "musteri", "tedarikci", "firma", "unvan", "cari unvan", "cari adi", "musteri adi",
             "cari unvani"],
    "aciklama": ["aciklama", "not", "notlar", "detay", "description", "aciklamalar"],
    "belge_no": ["belge", "belge no", "belge numarasi", "fis no", "fatura no", "evrak no", "dekont no",
                 "makbuz no"],
}
_AMOUNT_FIELDS = {"tutar", "gelir_tutar", "gider_tutar"}


def infer_hareket_mapping(rows: list[list[str]], ctx: Baglam) -> tuple[list[str], bool]:
    """(eşleştirme, ilk_satır_başlık_mı) döndürür."""
    if not rows:
        return [], False
    head = detect_header(rows[0], HAREKET_ESANLAM)
    if head:
        return _check_header_mapping(rows[1:81], head, ctx), True
    n = len(rows[0])
    sample = rows[:80]
    scores = []
    for c in range(n):
        vals = _values(sample, c)
        if not vals:
            scores.append(None)
            continue

        def frac(fn):
            return sum(1 for v in vals if fn(v)) / len(vals)

        scores.append({
            "tarih": frac(looks_like_date),
            "saat": frac(_is_time),
            "tutar": frac(_is_amount),
            "tur": frac(lambda v: tur_from_text(v) is not None),
            "kategori": frac(lambda v: bool(match_kategori(v, ctx, "GELIR") or match_kategori(v, ctx, "GIDER"))),
            "odeme": frac(lambda v: match_odeme(v, ctx) is not None),
            "cari": frac(lambda v: match_cari(v, ctx) is not None),
            "uzunluk": sum(len(v) for v in vals) / len(vals),
        })
    mapping = ["yok"] * n

    def take(f, key, thr):
        cands = [c for c in range(n) if mapping[c] == "yok" and scores[c]]
        if not cands:
            return
        best = max(cands, key=lambda c: scores[c][key])
        if scores[best][key] >= thr:
            mapping[best] = f

    take("tarih", "tarih", 0.7)
    take("saat", "saat", 0.8)
    take("tur", "tur", 0.7)
    take("odeme", "odeme", 0.6)
    take("kategori", "kategori", 0.5)
    take("cari", "cari", 0.5)
    amounts = [c for c in range(n) if mapping[c] == "yok" and scores[c] and scores[c]["tutar"] >= 0.8]
    if len(amounts) >= 2 and "tur" not in mapping:
        mapping[amounts[0]], mapping[amounts[1]] = "gelir_tutar", "gider_tutar"
    elif amounts:
        mapping[amounts[0]] = "tutar"
    texts = [c for c in range(n) if mapping[c] == "yok" and scores[c] and scores[c]["tutar"] < 0.5]
    if texts:
        if "kategori" not in mapping and len(texts) >= 2:
            short = min(texts, key=lambda c: scores[c]["uzunluk"])
            mapping[short] = "kategori"
            texts.remove(short)
        longest = max(texts, key=lambda c: scores[c]["uzunluk"])
        mapping[longest] = "aciklama"
    return mapping, False


def _check_header_mapping(sample: list[list[str]], mapping: list[str], ctx: Baglam) -> list[str]:
    """Başlık adı yanıltıcıysa düzeltir: yalnızca 'Ödeme' başlıklı ama içinde 'Nakit/Kart' yazan sütun
    ödeme tipi sayılır ve tersi. Başlıkla tanınan tutar sütunu asla 'tür' sütununa çevrilmez."""
    out = list(mapping)
    for c, f in enumerate(mapping):
        vals = _values(sample, c)
        if not vals:
            continue
        amount = sum(1 for v in vals if _is_amount(v)) / len(vals)
        if f in _AMOUNT_FIELDS and amount < 0.5:
            if "odeme" not in out and sum(1 for v in vals if match_odeme(v, ctx)) / len(vals) >= 0.6:
                out[c] = "odeme"
        elif f == "odeme" and amount >= 0.8 and "gider_tutar" not in out:
            out[c] = "gider_tutar"
    return out


def hazirla_hareketler(rows, mapping, ctx: Baglam, secenek: dict) -> list[HazirSatir]:
    """secenek: baslik_var, varsayilan_tarih (date), varsayilan_tur, varsayilan_odeme_id,
    kategori_olustur (bool), cari_olustur (bool)"""
    idx = {f: i for i, f in enumerate(mapping) if f != "yok"}
    start = 1 if secenek.get("baslik_var") else 0
    data_rows = rows[start:]
    out = []
    odeme_ad = {v[0]: v[1] for v in ctx.odemeler.values()}

    def get(r, f):
        i = idx.get(f)
        return r[i].strip() if i is not None and i < len(r) else ""

    # Tür sütunu yokken Tutar sütununda eksi değerler varsa (banka dökümü gibi) işaret türü belirler.
    signed = False
    if "tutar" in idx and "tur" not in idx:
        for r in data_rows:
            v = parse_amount(get(r, "tutar")) if not _blank(get(r, "tutar")) else None
            if v is not None and v < 0:
                signed = True
                break

    last_date = None
    for no, r in enumerate(data_rows, start=start + 1):
        s = HazirSatir(no=no)
        try:
            # --- tarih: boşsa bir üstteki satırın tarihi (kasa defterinde tarih günün ilk satırında yazar)
            t_raw = get(r, "tarih")
            if not _blank(t_raw):
                d = parse_date(t_raw)
                if not d:
                    raise _Hata(f"Tarih anlaşılamadı: '{t_raw}'")
                last_date = d
            else:
                d = last_date or secenek["varsayilan_tarih"]
                if last_date:
                    s.uyari = "Tarih boş, üstteki satırın tarihi kullanıldı"
            if not date_in_range(d):
                raise _Hata(f"Tarih ({fmt_date(d)}) geçersiz: 2000'den önce veya bir yıldan daha ileri.")

            # --- tür ve tutar
            tur_raw = get(r, "tur")
            tur_explicit = None
            if not _blank(tur_raw):
                tur_explicit = tur_from_text(tur_raw)
                if not tur_explicit:
                    raise _Hata(f"Tür anlaşılamadı: '{tur_raw}' (Gelir veya Gider yazın)")
            tur, tutar = None, None
            g_raw, c_raw = get(r, "gelir_tutar"), get(r, "gider_tutar")
            if not _blank(g_raw) or not _blank(c_raw):
                g = parse_amount(g_raw) if not _blank(g_raw) else 0
                c = parse_amount(c_raw) if not _blank(c_raw) else 0
                if g is None:
                    raise _Hata(f"Gelir tutarı anlaşılamadı: '{g_raw}'")
                if c is None:
                    raise _Hata(f"Gider tutarı anlaşılamadı: '{c_raw}'")
                if g < 0 or c < 0:
                    raise _Hata("Gelir/Gider sütununda eksi tutar var; düzeltme için karşı sütunu kullanın.")
                if g and c:
                    raise _Hata("Aynı satırda hem gelir hem gider tutarı var.")
                if g:
                    tur, tutar = "GELIR", g
                elif c:
                    tur, tutar = "GIDER", c
                if tur and tur_explicit and tur != tur_explicit:
                    raise _Hata("Tür sütunu ile gelir/gider sütunu çelişiyor.")
            t_amt = get(r, "tutar")
            if tutar is None and not _blank(t_amt):
                v = parse_amount(t_amt)
                if v is None:
                    raise _Hata(f"Tutar anlaşılamadı: '{t_amt}' (örnek: 1.250,50)")
                if tur_explicit:
                    if v < 0:
                        raise _Hata("Eksi tutar: tür sütunu varken tutar pozitif yazılmalı.")
                    tur = tur_explicit
                elif signed:
                    tur = "GELIR" if v > 0 else "GIDER"
                else:
                    tur = secenek.get("varsayilan_tur") or "GELIR"
                tutar = abs(v)
            if not tutar:
                raise _Hata("Tutar boş veya sıfır.")
            tur = tur or tur_explicit or secenek.get("varsayilan_tur") or "GELIR"
            tur_adi = "Gelir" if tur == "GELIR" else "Gider"

            # --- kategori
            k_raw = get(r, "kategori")
            kategori_id, kategori_ad = None, ""
            if not _blank(k_raw):
                k = match_kategori(k_raw, ctx, tur)
                if k:
                    if not k[2]:
                        raise _Hata(f"'{k[1]}' kategorisi pasif.")
                    kategori_id, kategori_ad = k[0], k[1]
                else:
                    other = "GIDER" if tur == "GELIR" else "GELIR"
                    if match_kategori(k_raw, ctx, other):
                        raise _Hata(f"'{k_raw}' bir {'gider' if other == 'GIDER' else 'gelir'} kategorisi; "
                                    f"satırın türü {tur_adi.lower()}.")
                    if secenek.get("kategori_olustur"):
                        kategori_ad = clean_text(k_raw, 60)
                        s.yeni_kategori = (tur, kategori_ad)
                        s.uyari = (s.uyari + "; " if s.uyari else "") + f"Yeni kategori açılacak: {kategori_ad}"
                    else:
                        raise _Hata(f"'{k_raw}' adında {tur_adi.lower()} kategorisi yok.")
            else:
                k = match_kategori("Diğer", ctx, tur)
                if not k or not k[2]:
                    raise _Hata("Kategori boş (ve aktif bir 'Diğer' kategorisi yok).")
                kategori_id, kategori_ad = k[0], k[1]
                s.uyari = (s.uyari + "; " if s.uyari else "") + "Kategori boş, 'Diğer' kullanılacak"

            # --- ödeme tipi
            o_raw = get(r, "odeme")
            if not _blank(o_raw):
                o = match_odeme(o_raw, ctx)
                if not o:
                    raise _Hata(f"'{o_raw}' ödeme tipi bulunamadı veya birden fazla ödeme tipine uyuyor.")
                if not o[2]:
                    raise _Hata(f"'{o[1]}' ödeme tipi pasif.")
                odeme_id = o[0]
            else:
                odeme_id = secenek.get("varsayilan_odeme_id")
                if not odeme_id:
                    raise _Hata("Ödeme tipi boş.")

            # --- cari
            cari_raw = get(r, "cari")
            cari_id, cari_ad = None, ""
            if not _blank(cari_raw):
                cm = match_cari(cari_raw, ctx)
                if cm:
                    if not cm[2]:
                        raise _Hata(f"'{cm[1]}' carisi pasif.")
                    cari_id, cari_ad = cm[0], cm[1]
                elif secenek.get("cari_olustur"):
                    cari_ad = clean_text(cari_raw, 150)
                    s.yeni_cari = cari_ad
                    s.uyari = (s.uyari + "; " if s.uyari else "") + f"Yeni cari açılacak: {cari_ad}"
                else:
                    raise _Hata(f"'{cari_raw}' adında cari kayıtlı değil.")

            if ctx.kilit and d.isoformat() <= ctx.kilit:
                raise _Hata(f"{fmt_date(d)} kapanmış bir güne ait.")

            s.veri = {"tarih": d.isoformat(), "saat": parse_time(get(r, "saat")), "tur": tur,
                      "kategori_id": kategori_id, "odeme_tipi_id": odeme_id, "cari_id": cari_id, "tutar": tutar,
                      "aciklama": clean_text(get(r, "aciklama"), 500), "belge_no": clean_text(get(r, "belge_no"), 60)}
            parts = [fmt_date(d), tur_adi, kategori_ad, odeme_ad.get(odeme_id, ""), fmt_money(tutar)]
            if cari_ad:
                parts.append(cari_ad)
            s.ozet = " · ".join(p for p in parts if p)
        except _Hata as e:
            s.hata = str(e)
        out.append(s)
    return out


# --------------------------------------------------------------------------- cariler
CARI_ALANLARI = [
    ("yok", "— Kullanma —"), ("unvan", "Ünvan"), ("kod", "Cari Kodu"), ("tip", "Cari Tipi"),
    ("yetkili", "Yetkili"), ("telefon", "Telefon"), ("eposta", "E-posta"), ("vergi_dairesi", "Vergi Dairesi"),
    ("vergi_no", "Vergi/TC No"), ("adres", "Adres"), ("notlar", "Notlar"),
    ("bakiye", "Açılış Bakiyesi (+ borçlu / − alacaklı)"), ("borc", "Açılış Borç"), ("alacak", "Açılış Alacak"),
]
CARI_ESANLAM = {
    "unvan": ["unvan", "unvani", "cari", "cari adi", "cari unvani", "firma", "firma adi", "musteri", "musteri adi",
              "tedarikci", "ad", "adi", "ad soyad", "isim", "ad soyad/unvan"],
    "kod": ["kod", "cari kodu", "cari kod", "musteri kodu"],
    "tip": ["tip", "tur", "cari tipi", "cari turu"],
    "yetkili": ["yetkili", "ilgili kisi", "yetkili kisi", "ilgili"],
    "telefon": ["telefon", "tel", "gsm", "cep", "telefon no", "cep telefonu", "tel no"],
    "eposta": ["e-posta", "eposta", "email", "e-mail", "mail"],
    "vergi_dairesi": ["vergi dairesi", "vd", "v.d.", "v.d"],
    "vergi_no": ["vergi no", "vkn", "tckn", "vergi numarasi", "tc no", "vergi/tc no", "vkn/tckn"],
    "adres": ["adres", "address", "acik adres"],
    "notlar": ["not", "notlar", "aciklama"],
    "bakiye": ["bakiye", "acilis bakiyesi", "devir", "devir bakiyesi", "bakiye (+b / -a)"],
    "borc": ["borc", "borc bakiyesi"],
    "alacak": ["alacak", "alacak bakiyesi"],
}


def _cari_tip(text, default):
    k = fold(text)
    if not k:
        return default
    if "ikisi" in k or "+" in k or ("mus" in k and "ted" in k) or k == "c":
        return "HER_IKISI"
    if k.startswith("mus") or k == "m":
        return "MUSTERI"
    if k.startswith("ted") or k.startswith("sat") or k == "t":
        return "TEDARIKCI"
    return None


def infer_cari_mapping(rows: list[list[str]]) -> tuple[list[str], bool]:
    """Başlıksız listelerde açılış bakiyesi sütunu kendiliğinden seçilmez (kullanıcı seçer)."""
    if not rows:
        return [], False
    head = detect_header(rows[0], CARI_ESANLAM)
    if head:
        return head, True
    n = len(rows[0])
    mapping = ["yok"] * n
    sample = rows[:80]
    for c in range(n):
        vals = _values(sample, c)
        if not vals:
            continue
        frac = lambda fn: sum(1 for v in vals if fn(v)) / len(vals)  # noqa: E731
        if "eposta" not in mapping and frac(lambda v: "@" in v) > 0.6:
            mapping[c] = "eposta"
        elif "telefon" not in mapping and frac(lambda v: bool(re.fullmatch(r"[+()0-9\s-]{10,}", v))
                                               and (" " in v or v.startswith(("0", "+", "5")))) > 0.6:
            mapping[c] = "telefon"
        elif "vergi_no" not in mapping and frac(lambda v: bool(re.fullmatch(r"\d{10,11}", v))) > 0.6:
            mapping[c] = "vergi_no"
        elif "tip" not in mapping and frac(lambda v: _cari_tip(v, None) is not None) > 0.8:
            mapping[c] = "tip"
        elif (c == 0 and "kod" not in mapping and n > 1
              and frac(lambda v: bool(re.fullmatch(r"[0-9A-Za-z.\-_/]{1,15}", v)) and any(ch.isdigit() for ch in v))
              > 0.8):
            mapping[c] = "kod"
    texts = [c for c in range(n) if mapping[c] == "yok" and _values(sample, c)
             and sum(1 for v in _values(sample, c) if _is_amount(v)) / len(_values(sample, c)) < 0.5]
    if texts:
        mapping[texts[0]] = "unvan"
        rest = texts[1:]
        if rest:
            longest = max(rest, key=lambda c: sum(len(r[c]) for r in sample))
            mapping[longest] = "adres"
    return mapping, False


def hazirla_cariler(rows, mapping, ctx: Baglam, secenek: dict) -> list[HazirSatir]:
    """secenek: baslik_var, varsayilan_tip, acilis_tarihi (date)
    Açılış bakiyesi: 'Bakiye' sütunu seçiliyse yalnız o kullanılır; değilse Borç − Alacak."""
    idx = {f: i for i, f in enumerate(mapping) if f != "yok"}
    start = 1 if secenek.get("baslik_var") else 0
    out, seen = [], set()

    def get(r, f):
        i = idx.get(f)
        return r[i].strip() if i is not None and i < len(r) else ""

    def amount(raw, label):
        if _blank(raw):
            return 0
        v = parse_amount(raw)
        if v is None:
            raise _Hata(f"{label} anlaşılamadı: '{raw}' (örnek: 1.250,50)")
        return v

    tip_adi = {"MUSTERI": "Müşteri", "TEDARIKCI": "Tedarikçi", "HER_IKISI": "Müşteri + Tedarikçi"}
    for no, r in enumerate(rows[start:], start=start + 1):
        s = HazirSatir(no=no)
        try:
            unvan = clean_text(get(r, "unvan"), 150)
            if not unvan:
                raise _Hata("Ünvan boş.")
            key = fold(unvan)
            if key in ctx.cariler:
                raise _Hata("Bu ünvanla kayıtlı cari zaten var.")
            if key in seen:
                raise _Hata("Aynı ünvan listede iki kez geçiyor.")
            tip = _cari_tip(get(r, "tip"), secenek.get("varsayilan_tip", "MUSTERI"))
            if tip is None:
                raise _Hata(f"Cari tipi anlaşılamadı: '{get(r, 'tip')}' (Müşteri / Tedarikçi)")
            if "bakiye" in idx:
                bakiye = amount(get(r, "bakiye"), "Bakiye")
            else:
                borc, alacak = amount(get(r, "borc"), "Borç"), amount(get(r, "alacak"), "Alacak")
                if borc < 0 or alacak < 0:
                    raise _Hata("Borç/Alacak sütununda eksi tutar olamaz.")
                bakiye = borc - alacak
            kod = clean_text(get(r, "kod"), 30)
            if kod and fold(kod) in ctx.cariler:
                raise _Hata(f"'{kod}' cari kodu kullanımda.")
            s.veri = {"unvan": unvan, "kod": kod, "tip": tip, "yetkili": get(r, "yetkili"),
                      "telefon": get(r, "telefon"), "eposta": get(r, "eposta"),
                      "vergi_dairesi": get(r, "vergi_dairesi"), "vergi_no": get(r, "vergi_no"),
                      "adres": get(r, "adres"), "notlar": get(r, "notlar")}
            if bakiye:
                s.acilis = {"tutar": abs(bakiye), "taraf": "BORC" if bakiye > 0 else "ALACAK",
                            "tarih": secenek.get("acilis_tarihi")}
            parts = [unvan, tip_adi[tip]]
            if s.veri["telefon"]:
                parts.append(s.veri["telefon"])
            if bakiye:
                parts.append("Açılış " + fmt_balance(bakiye))
            s.ozet = " · ".join(parts)
            seen.add(key)
        except _Hata as e:
            s.hata = str(e)
        out.append(s)
    return out
