"""Personel pencereleri: personel kartı, tek kayıt girişi (hak ediş, ödeme, avans…) ve toplu maaş."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPlainTextEdit, QTableWidget, QTableWidgetItem)

from ..errors import ServiceError
from ..services import MAAS, PERSONEL_FISLERI, PERSONEL_ISLEM_ADI, personel_durum
from ..util import TR_MONTHS, fmt_balance, fmt_date, fmt_money, fold, today, to_date
from .dialogs import BaseDialog
from .theme import C
from .widgets import (MoneyEdit, SearchCombo, Segmented, ask, button, combo, combo_select, date_edit, muted, pydate,
                      qdate)

ISLEMLER = ["HAK_EDIS", "ODEME", "AVANS", "BORC", "GERI_ODEME", "DEVREDEN_BORC", "DEVREDEN_ALACAK"]
HINTS = {
    "HAK_EDIS": "Kasayı etkilemez; personeli alacaklandırır (personele borcumuz artar). Örnek: ay sonu maaş hak edişi.",
    "ODEME": "Kasadan çıkış (gider); personeli borçlandırır (personele borcumuz azalır).",
    "AVANS": "Kasadan çıkış (gider, 'Personel Avansı'); personeli borçlandırır, sonraki hak edişten düşer.",
    "BORC": "Kasadan çıkış (gider, 'Personele Borç'); personeli borçlandırır.",
    "GERI_ODEME": "Kasaya giriş (gelir, 'Personel Geri Ödemesi'); personelin borcunu azaltır.",
    "DEVREDEN_BORC": "Kasayı etkilemez: programa geçmeden önceki açık avans/borç (personel bize borçlu).",
    "DEVREDEN_ALACAK": "Kasayı etkilemez: programa geçmeden önceki ödenmemiş hak ediş (personele borçluyuz).",
}


def bal_color(b) -> str:
    return C["gelir"] if (b or 0) > 0 else (C["gider"] if (b or 0) < 0 else C["muted"])


def bal_text(b) -> str:
    return f"{fmt_balance(b, True)} · {personel_durum(b)}"


def _kalem_combo(svc, keep=None):
    cb = combo()
    for k in svc.categories("GIDER"):
        if k["aktif"] or k["id"] == keep:
            cb.addItem(k["ad"] + ("" if k["aktif"] else " (pasif)"), k["id"])
    for i in range(cb.count()):
        if fold(cb.itemText(i)) == fold(MAAS):
            cb.setCurrentIndex(i)
            break
    return cb


def _odeme_combo(svc):
    return combo([(p["ad"], p["id"]) for p in svc.payment_types() if p["aktif"]])


# =========================================================================== personel kartı
class PersonelDialog(BaseDialog):
    def __init__(self, ctx, parent=None, personel: dict | None = None):
        self.ctx, self.svc = ctx, ctx.svc
        self.personel = personel
        self.saved_id = None
        super().__init__(parent, "Personel Bilgileri" if personel else "Yeni Personel",
                         "Personel hesabı cari gibi çalışır; bakiye, kayıtlı hak ediş ve ödemelerden hesaplanır.", 560)
        self.ad = QLineEdit()
        self.ad.setPlaceholderText("Ad soyad")
        self.gorev = QLineEdit()
        self.telefon = QLineEdit()
        self.iban = QLineEdit()
        self.iban.setPlaceholderText("TR..")
        self.ise_giris = QLineEdit()
        self.ise_giris.setPlaceholderText("gg.aa.yyyy (isteğe bağlı)")
        self.ise_giris.setMaximumWidth(180)
        self.notlar = QPlainTextEdit()
        self.notlar.setFixedHeight(54)
        self.add_row("Ad Soyad *", self.ad)
        self.add_row("Görev", self.gorev)
        self.add_row("Telefon", self.telefon)
        self.add_row("IBAN", self.iban)
        self.add_row("İşe Giriş Tarihi", self.ise_giris)
        self.add_row("Notlar", self.notlar)
        self.devreden = None
        if not personel:
            self.devreden = MoneyEdit()
            self.devreden.setFixedWidth(150)
            self.devreden_yon = Segmented([("ALACAK", "Personele borçluyuz"), ("BORC", "Personel bize borçlu")],
                                          tones={"ALACAK": "plain", "BORC": "plain"})
            self.devreden_yon.set_value("ALACAK")
            self.devreden_tarih = date_edit(today())
            arow = QHBoxLayout()
            arow.addWidget(self.devreden)
            arow.addWidget(self.devreden_yon)
            arow.addStretch(1)
            self.add_row("Devreden Bakiye", arow)
            drow = QHBoxLayout()
            drow.addWidget(self.devreden_tarih)
            drow.addWidget(muted("Varsa, programa geçmeden önceki bakiye (kasayı etkilemez)", small=True))
            drow.addStretch(1)
            self.add_row("Devreden Tarihi", drow)
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        self.save_btn = button("Kaydet", "check", "primary", callback=self._save)
        self.save_btn.setDefault(True)
        self.buttons.addWidget(self.save_btn)
        self.finish_layout()
        if personel:
            self.ad.setText(personel["ad_soyad"])
            self.gorev.setText(personel.get("gorev") or "")
            self.telefon.setText(personel.get("telefon") or "")
            self.iban.setText(personel.get("iban") or "")
            self.ise_giris.setText(fmt_date(personel.get("ise_giris")))
            self.notlar.setPlainText(personel.get("notlar") or "")
        self.ad.setFocus()

    def _save(self) -> None:
        data = {"ad_soyad": self.ad.text(), "gorev": self.gorev.text(), "telefon": self.telefon.text(),
                "iban": self.iban.text(), "ise_giris": self.ise_giris.text().strip(),
                "notlar": self.notlar.toPlainText()}
        devreden = None
        try:
            if self.devreden is not None and self.devreden.text().strip():
                if not self.devreden.is_valid():
                    raise ServiceError("Devreden bakiye anlaşılamadı. Örnek: 1.250,50")
                v = self.devreden.value()
                if v:
                    devreden = {"tutar": v, "yon": self.devreden_yon.value(),
                                "tarih": pydate(self.devreden_tarih.date())}
            self.saved_id = self.svc.save_personel(data, self.personel["id"] if self.personel else None,
                                                   devreden=devreden)
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("personel")
        self.accept()


# =========================================================================== tek kayıt
class PersonelKayitDialog(BaseDialog):
    """Yeni personel kaydı (fis=None) veya kasa dışı fişin (hak ediş, devreden) düzenlenmesi."""

    def __init__(self, ctx, parent=None, personel_id=None, fis: dict | None = None, islem: str = "HAK_EDIS"):
        self.ctx, self.svc = ctx, ctx.svc
        self.fis = fis
        self.saved = None
        super().__init__(parent, "Personel Kaydını Düzenle" if fis else "Personel Kaydı", width=580)
        keep = fis["personel_id"] if fis else personel_id
        self.personel = SearchCombo(placeholder="Personel seçin (yazarak arayın)…")
        self.personel.set_items([(p["ad_soyad"] + ("" if p["aktif"] else " (pasif)"), p["id"])
                                 for p in self.svc.personel_listesi(include_inactive=True)
                                 if p["aktif"] or p["id"] == keep])
        self.personel.set_current_id(keep)
        self.islem = combo([(PERSONEL_ISLEM_ADI[k], k) for k in (PERSONEL_FISLERI if fis else ISLEMLER)])
        self.hint = QLabel()
        self.hint.setObjectName("HintText")
        self.hint.setWordWrap(True)
        self.kalem = _kalem_combo(self.svc, keep=fis.get("kategori_id") if fis else None)
        self.tarih = date_edit(today())
        self.odeme = _odeme_combo(self.svc)
        self.tutar = MoneyEdit(big=True)
        self.tutar.setMinimumWidth(200)
        self.aciklama = QLineEdit()
        self.aciklama.setPlaceholderText("İsteğe bağlı")
        self.aciklama.setMaxLength(500)
        self.belge = QLineEdit()
        self.belge.setPlaceholderText("Dekont / makbuz no (isteğe bağlı)")
        self.belge.setMaxLength(60)
        self.dogrudan = QCheckBox("Hak edişi de kaydet (doğrudan ödeme)")
        self.dogrudan.setToolTip("Aynı kalem, tarih ve tutarda bir hak ediş de oluşturur; hesap bakiyesi değişmez.")
        self.bal = QLabel()
        self.bal.setObjectName("HintText")
        self.rows = {
            "personel": self.add_row("Personel *", self.personel),
            "islem": self.add_row("İşlem", self.islem),
            "hint": self.add_row("", self.hint),
            "kalem": self.add_row("Kalem", self.kalem),
            "tarih": self.add_row("Tarih", self.tarih),
            "odeme": self.add_row("Ödeme Tipi", self.odeme),
            "tutar": self.add_row("Tutar (₺)", self.tutar),
            "dogrudan": self.add_row("", self.dogrudan),
            "aciklama": self.add_row("Açıklama", self.aciklama),
            "belge": self.add_row("Belge No", self.belge),
            "bal": self.add_row("Güncel Bakiye", self.bal),
        }
        if fis:
            self.buttons.addWidget(button("Sil", "trash", "danger", callback=self._delete))
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("Kapat", callback=self.reject))
        self.save_btn = button("Kaydet", "check", "primary", callback=self._save)
        self.save_btn.setDefault(True)
        self.buttons.addWidget(self.save_btn)
        self.finish_layout()
        if fis:
            combo_select(self.islem, fis["islem"])
            if fis.get("kategori_id"):
                combo_select(self.kalem, fis["kategori_id"])
            self.tarih.setDate(qdate(to_date(fis["tarih"])))
            self.tutar.set_value(fis["tutar"])
            self.aciklama.setText(fis.get("aciklama") or "")
        else:
            combo_select(self.islem, islem)
        self.islem.currentIndexChanged.connect(lambda _: self._apply())
        self.personel.currentIndexChanged.connect(lambda _: self._show_balance())
        self._apply()
        self._show_balance()
        (self.tutar if self.personel.current_id() else self.personel).setFocus()

    def _apply(self) -> None:
        i = self.islem.currentData()
        kasa = i not in PERSONEL_FISLERI
        vis = {"kalem": i in ("HAK_EDIS", "ODEME"), "odeme": kasa, "belge": kasa, "dogrudan": i == "ODEME"}
        for key, on in vis.items():
            for w in self.rows[key]:
                w.setVisible(on)
        self.hint.setText(HINTS.get(i, ""))
        self.adjustSize()

    def _show_balance(self) -> None:
        pid = self.personel.current_id()
        if not pid or pid is SearchCombo.INVALID:
            self.bal.setText("")
            return
        b = self.svc.get_personel(pid)["bakiye"]
        self.bal.setText(bal_text(b))
        self.bal.setStyleSheet(f"color: {bal_color(b)}; font-weight: 700;")

    def collect(self) -> dict:
        pid = self.personel.current_id()
        if pid is SearchCombo.INVALID:
            raise ServiceError(f"'{self.personel.currentText()}' adında bir personel bulunamadı. Listeden seçin.")
        if not self.tutar.is_valid():
            self.tutar.set_invalid(True)
            raise ServiceError("Tutar anlaşılamadı. Örnek: 1.250,50")
        i = self.islem.currentData()
        kasa = i not in PERSONEL_FISLERI
        return {"personel_id": pid, "islem": i, "tarih": pydate(self.tarih.date()), "tutar": self.tutar.value(),
                "kategori_id": self.kalem.currentData() if i in ("HAK_EDIS", "ODEME") else None,
                "odeme_tipi_id": self.odeme.currentData() if kasa else None,
                "aciklama": self.aciklama.text(), "belge_no": self.belge.text() if kasa else "",
                "hak_edis_de": i == "ODEME" and self.dogrudan.isChecked()}

    def _save(self) -> None:
        try:
            data = self.collect()
            if self.fis:
                self.svc.update_personel_fisi(self.fis["id"], data)
                self.saved = {"hareket_id": None, "fis_id": self.fis["id"]}
            else:
                self.saved = self.svc.add_personel_kaydi(data)
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("hareket" if self.saved.get("hareket_id") else "personel")
        self.accept()

    def _delete(self) -> None:
        if not ask(self, "Kaydı Sil", f"Bu kayıt silinsin mi?\n\n{self.svc.describe_fis(self.fis)}", yes="Sil",
                   danger=True):
            return
        try:
            self.svc.delete_personel_fisi(self.fis["id"])
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("personel")
        self.accept()


# =========================================================================== toplu maaş
class TopluMaasDialog(BaseDialog):
    def __init__(self, ctx, parent=None):
        self.ctx, self.svc = ctx, ctx.svc
        self.result_counts: dict = {}
        super().__init__(parent, "Toplu Maaş",
                         "Hak ediş: kasayı etkilemez, personeli alacaklandırır. Ödenen: kasadan çıkar. "
                         "Boş satırlar atlanır; tüm satırlar tek işlemde kaydedilir.", 820)
        self.tarih = date_edit(today())
        self.kalem = _kalem_combo(self.svc)
        self.odeme = _odeme_combo(self.svc)
        self.aciklama = QLineEdit(self._default_text())
        self.aciklama.setMaxLength(500)
        top = QHBoxLayout()
        top.addWidget(self.tarih)
        for text, w in (("Kalem", self.kalem), ("Ödeme Tipi", self.odeme)):
            lab = QLabel(text)
            lab.setProperty("muted", True)
            top.addSpacing(8)
            top.addWidget(lab)
            top.addWidget(w)
        top.addStretch(1)
        self.add_row("Tarih", top)
        self.add_row("Açıklama", self.aciklama)
        self.v.addLayout(self.grid)            # finish_layout yerine: tablo üst alanlarla düğmeler arasına girer
        self.people = self.svc.personel_listesi()
        headers = ["Ad Soyad", "Bakiye", "Hak Ediş", "Ödenen"]
        self.table = QTableWidget(len(self.people), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setMinimumSectionSize(130)
        self.edits = []
        for i, p in enumerate(self.people):
            it = QTableWidgetItem(p["ad_soyad"])
            self.table.setItem(i, 0, it)
            b = QTableWidgetItem(fmt_balance(p["bakiye"]))
            b.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            b.setForeground(QColor(bal_color(p["bakiye"])))
            self.table.setItem(i, 1, b)
            hak, ode = MoneyEdit(), MoneyEdit()
            for j, e in ((2, hak), (3, ode)):
                e.setMinimumWidth(130)
                e.textChanged.connect(lambda _=None: self._totals())
                self.table.setCellWidget(i, j, e)
            self.edits.append((p, hak, ode))
        self.table.setMinimumHeight(min(460, 44 + 34 * max(1, len(self.people))))
        self.v.addWidget(self.table, 1)
        frow = QHBoxLayout()
        frow.addWidget(button("Ödenecekleri doldur", "copy", tip="Ödenen = hak edişten sonra personele borcumuz",
                              callback=self._fill))
        self.footer = QLabel()
        frow.addWidget(self.footer, 1)
        self.v.addLayout(frow)
        self.v.addWidget(self.msg)
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        self.save_btn = button("Kaydet", "check", "primary", callback=self._save)
        self.buttons.addWidget(self.save_btn)
        self.v.addLayout(self.buttons)
        self.tarih.dateChanged.connect(self._date_changed)
        self._last_default = self.aciklama.text()
        self._totals()

    def _default_text(self) -> str:
        d = pydate(self.tarih.date())
        return f"{TR_MONTHS[d.month - 1]} {d.year} maaşı"

    def _date_changed(self, *_):
        # kullanıcı açıklamayı değiştirmediyse yeni tarihin ayına göre güncellenir
        if self.aciklama.text() == self._last_default:
            self.aciklama.setText(self._default_text())
        self._last_default = self._default_text()

    def _fill(self) -> None:
        for p, hak, ode in self.edits:
            h = (hak.value() or 0) if hak.is_valid() else 0
            v = max(0, h - p["bakiye"])         # hak edişten sonra personele borcumuz
            ode.set_value(v if v else None)

    def _rows(self, strict: bool = False) -> list[dict]:
        out = []
        for p, hak, ode in self.edits:
            if strict and not (hak.is_valid() and ode.is_valid()):
                (hak if not hak.is_valid() else ode).set_invalid(True)
                raise ServiceError(f"{p['ad_soyad']}: tutar anlaşılamadı. Örnek: 1.250,50")
            h = (hak.value() or 0) if hak.is_valid() else 0
            o = (ode.value() or 0) if ode.is_valid() else 0
            if h or o:
                out.append({"personel_id": p["id"], "hak_edis": h, "odenen": o})
        return out

    def _totals(self) -> None:
        rows = self._rows()
        self.footer.setText(
            f"<span style='color:{C['muted']}'>{len(rows)} kişi</span> &nbsp;·&nbsp; "
            f"Toplam hak ediş <b>{fmt_money(sum(r['hak_edis'] for r in rows))} ₺</b> &nbsp;·&nbsp; "
            f"Toplam ödeme <b style='color:{C['gider']}'>{fmt_money(sum(r['odenen'] for r in rows))} ₺</b>")

    def _save(self) -> None:
        try:
            rows = self._rows(strict=True)
            if not rows:
                raise ServiceError("Kaydedilecek tutar yok. Hak Ediş veya Ödenen sütununa tutar yazın.")
            self.result_counts = self.svc.personel_toplu_kaydet(pydate(self.tarih.date()), self.kalem.currentData(),
                                                                self.odeme.currentData(), self.aciklama.text(), rows)
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("hareket")
        self.accept()
