"""Kayıt pencereleri: hareket, cari, kullanıcı, şifre, gün kapanışı, kurtarma anahtarı, yardım."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
                               QHeaderView, QAbstractItemView)

from .. import APP_COPYRIGHT, APP_TITLE, __version__
from ..errors import ServiceError
from ..services import KASA_TURLERI, ROL_ADI
from ..util import fmt_date, fmt_money, today, to_date
from .theme import C, TUR_RENK
from .widgets import (MoneyEdit, SearchCombo, Segmented, ask, button, combo, combo_select, date_edit, pydate, qdate,
                      set_prop, time_edit, muted)


class BaseDialog(QDialog):
    def __init__(self, parent=None, title: str = "", subtitle: str = "", width: int = 520):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(22, 18, 22, 16)
        self.v.setSpacing(12)
        if title:
            t = QLabel(title)
            t.setObjectName("SectionTitle")
            self.v.addWidget(t)
        if subtitle:
            self.v.addWidget(muted(subtitle))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(9)
        self.grid.setColumnStretch(1, 1)
        self._row = 0
        self.msg = QLabel()
        self.msg.setWordWrap(True)
        self.msg.hide()
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)

    def exec(self) -> int:
        """Pencere kapandıktan sonra silinir. Aksi halde her açılan pencere ana pencerenin alt nesnesi olarak
        program kapanana kadar bellekte kalır (gün boyu birikir) ve kapanışta Qt'nin nesne silme sırası nedeniyle
        programın çökmesine yol açabilir. deleteLater, ancak denetim bu satırın çalıştığı olay döngüsüne dönünce
        uygulanır; bu yüzden exec() dönüşünden hemen sonra pencerenin alanları okunabilir."""
        try:
            return super().exec()
        finally:
            self.deleteLater()

    def add_row(self, label: str, w, key: str | None = None):
        lab = QLabel(label)
        lab.setProperty("muted", True)
        lab.setMinimumWidth(110)
        self.grid.addWidget(lab, self._row, 0, Qt.AlignVCenter)
        if not isinstance(w, QWidget):
            holder = QWidget()
            w.setContentsMargins(0, 0, 0, 0)
            holder.setLayout(w)
            w = holder
        self.grid.addWidget(w, self._row, 1)
        self._row += 1
        return lab, w

    def finish_layout(self) -> None:
        self.v.addLayout(self.grid)
        self.v.addWidget(self.msg)
        self.v.addStretch(1)
        self.v.addLayout(self.buttons)

    def show_error(self, text: str) -> None:
        self.msg.setObjectName("ErrorText")
        self.msg.setText(text)
        set_prop(self.msg, "x", 1)
        self.msg.show()

    def show_ok(self, text: str) -> None:
        self.msg.setObjectName("OkText")
        self.msg.setText(text)
        set_prop(self.msg, "x", 2)
        self.msg.show()

    def clear_msg(self) -> None:
        self.msg.hide()


# =========================================================================== hareket
class MovementDialog(BaseDialog):
    saved = Signal(int)

    TYPES = [("GELIR", "Gelir"), ("GIDER", "Gider"), ("VIRMAN", "Virman"), ("CARI_BORC", "Cari Borç"),
             ("CARI_ALACAK", "Cari Alacak")]
    HINTS = {
        "GELIR": "Kasaya giren para. Cari seçerseniz tutar o carinin hesabına tahsilat (alacak) olarak işlenir.",
        "GIDER": "Kasadan çıkan para. Cari seçerseniz tutar o carinin hesabına ödeme (borç) olarak işlenir.",
        "VIRMAN": "Bir ödeme tipinden diğerine aktarım (ör. nakdin bankaya yatırılması). Gelir veya gider sayılmaz.",
        "CARI_BORC": "Kasayı etkilemez; cariyi borçlandırır. Örnek: müşteriye veresiye kargo gönderimi.",
        "CARI_ALACAK": "Kasayı etkilemez; cariyi alacaklandırır. Örnek: tedarikçiden veresiye alınan hizmet/fatura.",
    }

    def __init__(self, ctx, parent=None, tur: str = "GELIR", movement: dict | None = None,
                 defaults: dict | None = None):
        self.ctx, self.svc = ctx, ctx.svc
        self.movement = movement
        super().__init__(parent, "Kaydı Düzenle" if movement else "Yeni Kayıt", width=580)
        self.seg = Segmented(self.TYPES, tones={k: k for k, _ in self.TYPES})
        self.v.addWidget(self.seg)
        self.hint = QLabel()
        self.hint.setObjectName("HintText")
        self.hint.setWordWrap(True)
        self.v.addWidget(self.hint)

        self.tarih = date_edit()
        self.saat = time_edit()
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(self.tarih)
        lab = QLabel("Saat")
        lab.setProperty("muted", True)
        trow.addSpacing(8)
        trow.addWidget(lab)
        trow.addWidget(self.saat)
        trow.addStretch(1)
        self.kategori = combo()
        self.odeme = combo()
        self.hedef = combo()
        self.cari = SearchCombo(placeholder="Cari yok — seçmek için yazın")
        self.cari_add = button("", "plus", tip="Yeni cari ekle")
        crow = QHBoxLayout()
        crow.setSpacing(6)
        crow.addWidget(self.cari, 1)
        crow.addWidget(self.cari_add)
        self.tutar = MoneyEdit(big=True)
        self.tutar.setMinimumWidth(200)
        self.aciklama = QLineEdit()
        self.aciklama.setPlaceholderText("İsteğe bağlı")
        self.aciklama.setMaxLength(500)
        self.belge = QLineEdit()
        self.belge.setPlaceholderText("Fiş / fatura / dekont no (isteğe bağlı)")
        self.belge.setMaxLength(60)
        self.bal_hint = QLabel()
        self.bal_hint.setObjectName("HintText")
        self.bal_hint.setWordWrap(True)
        self.rows = {
            "tarih": self.add_row("Tarih", trow),
            "kategori": self.add_row("Kategori", self.kategori),
            "odeme": self.add_row("Ödeme Tipi", self.odeme),
            "hedef": self.add_row("Giren (hedef)", self.hedef),
            "cari": self.add_row("Cari", crow),
            "tutar": self.add_row("Tutar (₺)", self.tutar),
            "aciklama": self.add_row("Açıklama", self.aciklama),
            "belge": self.add_row("Belge No", self.belge),
        }
        self.grid.addWidget(self.bal_hint, self._row, 1)
        self._row += 1

        self.del_btn = None
        if movement:
            self.del_btn = button("Sil", "trash", "danger", callback=self._delete)
            self.buttons.addWidget(self.del_btn)
        self.buttons.addStretch(1)
        self.cancel_btn = button("Kapat", callback=self.reject)
        self.buttons.addWidget(self.cancel_btn)
        self.save_new_btn = None
        if not movement:
            self.save_new_btn = button("Kaydet ve Yeni", "plus", tip="Ctrl+Enter: kaydet ve yeni kayda geç",
                                       callback=lambda: self._save(again=True))
            self.buttons.addWidget(self.save_new_btn)
        self.save_btn = button("Kaydet", "check", "primary", tip="Enter", callback=lambda: self._save(False))
        self.save_btn.setDefault(True)
        self.buttons.addWidget(self.save_btn)
        self.finish_layout()

        self._fill_lists()
        if movement:
            self.seg.set_value(movement["tur"])
            self._apply_type()
            self._load(movement)
        else:
            self.seg.set_value(tur)
            self._apply_type()
            if defaults:
                self._load(defaults, is_default=True)
        self.seg.changed.connect(lambda _: self._apply_type())
        self.odeme.currentIndexChanged.connect(self._update_hint)
        self.hedef.currentIndexChanged.connect(self._update_hint)
        self.tarih.dateChanged.connect(self._update_hint)
        self.cari_add.clicked.connect(self._new_cari)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=lambda: self._save(again=not self.movement))
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=lambda: self._save(again=not self.movement))
        self._update_hint()
        self._check_editable()
        self._initial_focus()

    # ------------------------------------------------------------ listeler
    def _fill_lists(self) -> None:
        keep = self.movement or {}
        self._cats = self.svc.categories()
        pts = [p for p in self.svc.payment_types()
               if p["aktif"] or p["id"] in (keep.get("odeme_tipi_id"), keep.get("hedef_odeme_tipi_id"))]
        for cb in (self.odeme, self.hedef):
            cb.clear()
            for p in pts:
                cb.addItem(p["ad"] + ("" if p["aktif"] else " (pasif)"), p["id"])
        if self.hedef.count() > 1:
            self.hedef.setCurrentIndex(1)
        caris = [c for c in self.svc.caris(include_inactive=True) if c["aktif"] or c["id"] == keep.get("cari_id")]
        self.cari.set_items([(c["unvan"], c["id"]) for c in caris], none_text="")

    def _fill_categories(self, tur: str) -> None:
        cur = self.kategori.currentData()
        self.kategori.blockSignals(True)
        self.kategori.clear()
        keep = (self.movement or {}).get("kategori_id")
        for k in self._cats:
            if k["tur"] == tur and (k["aktif"] or k["id"] == keep):
                self.kategori.addItem(k["ad"] + ("" if k["aktif"] else " (pasif)"), k["id"])
        combo_select(self.kategori, cur)
        self.kategori.blockSignals(False)

    def _apply_type(self) -> None:
        t = self.seg.value() or "GELIR"
        kasa = t in KASA_TURLERI
        show = {
            "kategori": t in ("GELIR", "GIDER"), "odeme": kasa, "hedef": t == "VIRMAN",
            "cari": t != "VIRMAN",
        }
        for key, vis in show.items():
            for w in self.rows[key]:
                w.setVisible(vis)
        self.rows["odeme"][0].setText("Çıkan (kaynak)" if t == "VIRMAN" else "Ödeme Tipi")
        self.rows["cari"][0].setText("Cari *" if t in ("CARI_BORC", "CARI_ALACAK") else "Cari")
        self.saat.setEnabled(True)
        if t in ("GELIR", "GIDER"):
            self._fill_categories(t)
        self.hint.setText(self.HINTS[t])
        self.hint.setStyleSheet(f"color: {TUR_RENK[t]};")
        self.save_btn.setProperty("kind", {"GELIR": "gelir", "GIDER": "gider", "VIRMAN": "virman"}.get(t, "cari"))
        set_prop(self.save_btn, "kind", self.save_btn.property("kind"))
        self._update_hint()
        self.adjustSize()

    def _update_hint(self, *_):
        t = self.seg.value()
        d = pydate(self.tarih.date())
        parts = []
        if t in KASA_TURLERI:
            if self.svc.is_locked(d) and not self.movement:
                parts.append(f"⚠ {fmt_date(d)} kapatılmış bir gün; kasa kaydı eklenemez.")
            bal = self.svc.balances_before(d + dt.timedelta(days=1))
            pid = self.odeme.currentData()
            if pid in bal:
                parts.append(f"{self.odeme.currentText()} bakiyesi ({fmt_date(d)} sonu): {fmt_money(bal[pid])} ₺")
            if t == "VIRMAN" and self.hedef.currentData() in bal:
                parts.append(f"{self.hedef.currentText()}: {fmt_money(bal[self.hedef.currentData()])} ₺")
        self.bal_hint.setText("   ·   ".join(parts))

    # ------------------------------------------------------------ yükleme
    def _load(self, m: dict, is_default: bool = False) -> None:
        if m.get("tarih"):
            self.tarih.setDate(qdate(to_date(m["tarih"])))
        if m.get("saat") and not is_default:
            h, mi = m["saat"].split(":")
            self.saat.setTime(QTime(int(h), int(mi)))
        if m.get("kategori_id"):
            combo_select(self.kategori, m["kategori_id"])
        if m.get("odeme_tipi_id"):
            combo_select(self.odeme, m["odeme_tipi_id"])
        if m.get("hedef_odeme_tipi_id"):
            combo_select(self.hedef, m["hedef_odeme_tipi_id"])
        if "cari_id" in m:
            self.cari.set_current_id(m.get("cari_id"))
        if m.get("tutar"):
            self.tutar.set_value(m["tutar"])
        self.aciklama.setText(m.get("aciklama") or "")
        self.belge.setText(m.get("belge_no") or "")

    def _check_editable(self) -> None:
        m = self.movement
        if m and m["tur"] in KASA_TURLERI and self.svc.is_locked(m["tarih"]):
            for w in (self.seg, self.tarih, self.saat, self.kategori, self.odeme, self.hedef, self.cari,
                      self.cari_add, self.tutar, self.aciklama, self.belge, self.save_btn):
                w.setEnabled(False)
            if self.del_btn:
                self.del_btn.setEnabled(False)
            self.show_error(f"Bu kayıt kapatılmış bir güne ({fmt_date(m['tarih'])}) ait; değiştirilemez. "
                            "Gerekirse yönetici Günlük Kasa ekranından günü yeniden açabilir.")

    def _initial_focus(self) -> None:
        t = self.seg.value()
        if self.movement:
            self.tutar.setFocus()
            self.tutar.selectAll()
        elif t in ("GELIR", "GIDER") and not self.kategori.currentData():
            self.kategori.setFocus()
        elif t in ("CARI_BORC", "CARI_ALACAK") and self.cari.current_id() is None:
            self.cari.setFocus()
        else:
            self.tutar.setFocus()

    # ------------------------------------------------------------ işlemler
    def collect(self) -> dict:
        t = self.seg.value()
        cid = self.cari.current_id()
        if cid is SearchCombo.INVALID:
            raise ServiceError(f"'{self.cari.currentText()}' adında bir cari bulunamadı. Listeden seçin veya "
                               "+ düğmesiyle yeni cari ekleyin.")
        if not self.tutar.is_valid():
            self.tutar.set_invalid(True)
            raise ServiceError("Tutar anlaşılamadı. Örnek: 1.250,50")
        return {
            "tur": t, "tarih": pydate(self.tarih.date()), "saat": self.saat.time().toString("HH:mm"),
            "kategori_id": self.kategori.currentData() if t in ("GELIR", "GIDER") else None,
            "odeme_tipi_id": self.odeme.currentData() if t in KASA_TURLERI else None,
            "hedef_odeme_tipi_id": self.hedef.currentData() if t == "VIRMAN" else None,
            "cari_id": cid if t != "VIRMAN" else None,
            "tutar": self.tutar.value(), "aciklama": self.aciklama.text(), "belge_no": self.belge.text(),
        }

    def _save(self, again: bool = False) -> None:
        if not self.save_btn.isEnabled():
            return
        try:
            data = self.collect()
            if self.movement:
                self.svc.update_movement(self.movement["id"], data)
                mid = self.movement["id"]
            else:
                mid = self.svc.add_movement(data)
        except ServiceError as e:
            self.show_error(str(e))
            if "Tutar" in str(e):
                self.tutar.set_invalid(True)
                self.tutar.setFocus()
            return
        self.saved.emit(mid)
        self.ctx.bus.changed.emit("hareket")
        if again:
            m = self.svc.get_movement(mid)
            self.show_ok(f"✓ Kaydedildi: {m['tur_adi']} · {m['kategori_gosterim']} · {fmt_money(m['tutar'])} ₺")
            self.tutar.clear()
            self.aciklama.clear()
            self.belge.clear()
            self.cari.set_current_id(None)
            self.saat.setTime(QTime.currentTime())
            self._update_hint()
            if self.seg.value() in ("GELIR", "GIDER"):
                self.kategori.setFocus()
            else:
                self.tutar.setFocus()
        else:
            self.accept()

    def _delete(self) -> None:
        m = self.movement
        if not ask(self, "Kaydı Sil", f"Bu kayıt silinsin mi?\n\n{self.svc.describe(m)}", yes="Sil", danger=True):
            return
        try:
            self.svc.delete_movement(m["id"])
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("hareket")
        self.accept()

    def _new_cari(self) -> None:
        tip = "MUSTERI" if self.seg.value() in ("GELIR", "CARI_BORC") else "TEDARIKCI"
        dlg = CariDialog(self.ctx, self, default_tip=tip, initial_name=self.cari.currentText().strip()
                         if self.cari.current_id() is SearchCombo.INVALID else "")
        if dlg.exec() and dlg.saved_id:
            self._fill_lists()
            self.cari.set_current_id(dlg.saved_id)


# =========================================================================== cari
class CariDialog(BaseDialog):
    def __init__(self, ctx, parent=None, cari: dict | None = None, default_tip: str = "MUSTERI",
                 initial_name: str = ""):
        self.ctx, self.svc = ctx, ctx.svc
        self.cari = cari
        self.saved_id = None
        super().__init__(parent, "Cari Bilgileri" if cari else "Yeni Cari",
                         "Müşteri veya tedarikçi hesabı. Bakiye, kayıtlı hareketlerden otomatik hesaplanır.", 600)
        self.unvan = QLineEdit()
        self.unvan.setPlaceholderText("Firma ünvanı veya ad soyad")
        self.tip = Segmented([("MUSTERI", "Müşteri"), ("TEDARIKCI", "Tedarikçi"), ("HER_IKISI", "Her İkisi")],
                             tones={"MUSTERI": "cari", "TEDARIKCI": "cari", "HER_IKISI": "cari"})
        self.kod = QLineEdit()
        self.kod.setPlaceholderText("Boş bırakılırsa otomatik verilir")
        self.yetkili = QLineEdit()
        self.telefon = QLineEdit()
        self.eposta = QLineEdit()
        self.vd = QLineEdit()
        self.vno = QLineEdit()
        self.adres = QPlainTextEdit()
        self.adres.setFixedHeight(58)
        self.notlar = QPlainTextEdit()
        self.notlar.setFixedHeight(48)
        self.add_row("Ünvan *", self.unvan)
        trow = QHBoxLayout()
        trow.addWidget(self.tip)
        trow.addStretch(1)
        self.add_row("Cari Tipi", trow)
        self.add_row("Cari Kodu", self.kod)
        self.add_row("Yetkili", self.yetkili)
        two = QHBoxLayout()
        two.addWidget(self.telefon, 1)
        lab = QLabel("E-posta")
        lab.setProperty("muted", True)
        two.addWidget(lab)
        two.addWidget(self.eposta, 1)
        self.add_row("Telefon", two)
        vrow = QHBoxLayout()
        vrow.addWidget(self.vd, 1)
        lab2 = QLabel("Vergi/TC No")
        lab2.setProperty("muted", True)
        vrow.addWidget(lab2)
        vrow.addWidget(self.vno, 1)
        self.add_row("Vergi Dairesi", vrow)
        self.add_row("Adres", self.adres)
        self.add_row("Notlar", self.notlar)
        self.aktif = None
        self.acilis = None
        if cari:
            self.aktif = QCheckBox("Aktif (pasif cariler listelerde ve seçimlerde görünmez)")
            self.add_row("", self.aktif)
        else:
            self.acilis = MoneyEdit()
            self.acilis.setFixedWidth(150)
            self.acilis_taraf = Segmented([("BORC", "Bize borçlu"), ("ALACAK", "Biz borçluyuz")],
                                          tones={"BORC": "plain", "ALACAK": "plain"})
            self.acilis_taraf.set_value("BORC")
            self.acilis_tarih = date_edit(today())
            arow = QHBoxLayout()
            arow.addWidget(self.acilis)
            arow.addWidget(self.acilis_taraf)
            arow.addStretch(1)
            self.add_row("Açılış Bakiyesi", arow)
            drow = QHBoxLayout()
            drow.addWidget(self.acilis_tarih)
            drow.addWidget(muted("Varsa, programa geçmeden önceki bakiye", small=True))
            drow.addStretch(1)
            self.add_row("Açılış Tarihi", drow)
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        self.save_btn = button("Kaydet", "check", "primary", callback=self._save)
        self.save_btn.setDefault(True)
        self.buttons.addWidget(self.save_btn)
        self.finish_layout()
        if cari:
            self.unvan.setText(cari["unvan"])
            self.tip.set_value(cari["tip"])
            self.kod.setText(cari.get("kod") or "")
            self.yetkili.setText(cari.get("yetkili") or "")
            self.telefon.setText(cari.get("telefon") or "")
            self.eposta.setText(cari.get("eposta") or "")
            self.vd.setText(cari.get("vergi_dairesi") or "")
            self.vno.setText(cari.get("vergi_no") or "")
            self.adres.setPlainText(cari.get("adres") or "")
            self.notlar.setPlainText(cari.get("notlar") or "")
            self.aktif.setChecked(bool(cari["aktif"]))
        else:
            self.tip.set_value(default_tip)
            self.unvan.setText(initial_name)
        self.unvan.setFocus()

    def _save(self) -> None:
        data = {"unvan": self.unvan.text(), "tip": self.tip.value(), "kod": self.kod.text(),
                "yetkili": self.yetkili.text(), "telefon": self.telefon.text(), "eposta": self.eposta.text(),
                "vergi_dairesi": self.vd.text(), "vergi_no": self.vno.text(), "adres": self.adres.toPlainText(),
                "notlar": self.notlar.toPlainText()}
        acilis = None
        try:
            if self.acilis is not None and self.acilis.text().strip():
                if not self.acilis.is_valid():
                    raise ServiceError("Açılış bakiyesi anlaşılamadı. Örnek: 1.250,50")
                v = self.acilis.value()
                if v:
                    acilis = {"tutar": v, "taraf": self.acilis_taraf.value(),
                              "tarih": pydate(self.acilis_tarih.date())}
            self.saved_id = self.svc.save_cari(data, self.cari["id"] if self.cari else None, acilis=acilis)
            if self.cari and self.aktif is not None and bool(self.aktif.isChecked()) != bool(self.cari["aktif"]):
                self.svc.set_cari_active(self.cari["id"], self.aktif.isChecked())
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("cari")
        self.accept()


# =========================================================================== kullanıcı
class UserDialog(BaseDialog):
    def __init__(self, ctx, parent=None, user: dict | None = None):
        self.ctx, self.svc = ctx, ctx.svc
        self.user = user
        super().__init__(parent, "Kullanıcıyı Düzenle" if user else "Yeni Kullanıcı",
                         "" if user else "Yeni kullanıcı ilk girişte kendi şifresini belirlemek zorundadır.", 480)
        self.name = QLineEdit()
        self.full = QLineEdit()
        self.rol = combo([(v, k) for k, v in ROL_ADI.items()])
        self.add_row("Kullanıcı Adı", self.name)
        self.add_row("Ad Soyad", self.full)
        self.add_row("Rol", self.rol)
        self.add_row("", muted("Yönetici: her şey.  Kullanıcı: kayıt girer/düzenler, günü kapatır; tanımları, "
                               "kullanıcıları ve kapanmış günleri değiştiremez.", small=True))
        self.aktif = None
        if user:
            self.name.setText(user["kullanici_adi"])
            self.name.setEnabled(False)
            self.full.setText(user["ad_soyad"])
            combo_select(self.rol, user["rol"])
            self.aktif = QCheckBox("Aktif (pasif kullanıcı giriş yapamaz)")
            self.aktif.setChecked(bool(user["aktif"]))
            self.add_row("", self.aktif)
        else:
            combo_select(self.rol, "kullanici")
            self.pw1 = QLineEdit()
            self.pw1.setEchoMode(QLineEdit.Password)
            self.pw2 = QLineEdit()
            self.pw2.setEchoMode(QLineEdit.Password)
            self.add_row("Geçici Şifre", self.pw1)
            self.add_row("Şifre (tekrar)", self.pw2)
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        b = button("Kaydet", "check", "primary", callback=self._save)
        b.setDefault(True)
        self.buttons.addWidget(b)
        self.finish_layout()

    def _save(self) -> None:
        try:
            if self.user:
                self.svc.update_user(self.user["id"], self.full.text(), self.rol.currentData(), self.aktif.isChecked())
            else:
                if self.pw1.text() != self.pw2.text():
                    raise ServiceError("Şifreler aynı değil.")
                self.svc.create_user(self.name.text(), self.full.text(), self.pw1.text(), self.rol.currentData())
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("kullanici")
        self.accept()


class PasswordDialog(BaseDialog):
    """mode: change (kendi şifresi) | force (ilk giriş) | reset (yönetici başkasınınkini sıfırlar)"""

    def __init__(self, ctx, parent=None, mode: str = "change", user: dict | None = None):
        self.ctx, self.svc = ctx, ctx.svc
        self.mode, self.user = mode, user
        titles = {"change": "Şifre Değiştir", "force": "Yeni Şifre Belirleyin",
                  "reset": f"Şifre Sıfırla: {user['kullanici_adi'] if user else ''}"}
        subs = {"change": "", "force": "Güvenliğiniz için ilk girişte kendinize ait yeni bir şifre belirleyin.",
                "reset": "Kullanıcı bir sonraki girişinde bu şifreyi değiştirmek zorunda olacak."}
        super().__init__(parent, titles[mode], subs[mode], 440)
        self.old = None
        if mode == "change":
            self.old = QLineEdit()
            self.old.setEchoMode(QLineEdit.Password)
            self.add_row("Mevcut Şifre", self.old)
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        self.add_row("Yeni Şifre", self.pw1)
        self.add_row("Yeni Şifre (tekrar)", self.pw2)
        self.add_row("", muted("En az 4 karakter.", small=True))
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        b = button("Kaydet", "check", "primary", callback=self._save)
        b.setDefault(True)
        self.buttons.addWidget(b)
        self.finish_layout()
        (self.old or self.pw1).setFocus()

    def _save(self) -> None:
        try:
            if self.pw1.text() != self.pw2.text():
                raise ServiceError("Yeni şifreler aynı değil.")
            if self.mode == "change":
                self.svc.change_own_password(self.old.text(), self.pw1.text())
            elif self.mode == "force":
                self.svc.change_own_password("", self.pw1.text(), check_old=False)
            else:
                self.svc.reset_password(self.user["id"], self.pw1.text())
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.accept()


class TextDialog(BaseDialog):
    def __init__(self, parent, title: str, label: str, text: str = "", subtitle: str = ""):
        super().__init__(parent, title, subtitle, 420)
        self.edit = QLineEdit(text)
        self.add_row(label, self.edit)
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        b = button("Tamam", "check", "primary", callback=self.accept)
        b.setDefault(True)
        self.buttons.addWidget(b)
        self.finish_layout()
        self.edit.selectAll()
        self.edit.setFocus()

    def value(self) -> str:
        return self.edit.text().strip()


class PaymentTypeDialog(BaseDialog):
    def __init__(self, ctx, parent=None, pt: dict | None = None):
        self.ctx, self.svc, self.pt = ctx, ctx.svc, pt
        super().__init__(parent, "Ödeme Tipini Düzenle" if pt else "Yeni Ödeme Tipi",
                         "Her ödeme tipinin kendi bakiyesi vardır ve günden güne devreder.", 480)
        self.ad = QLineEdit(pt["ad"] if pt else "")
        self.acilis = MoneyEdit(allow_negative=True)
        self.acilis.set_value(pt["acilis_bakiye"] if pt else 0)
        self.acilis.setMaximumWidth(200)
        self.add_row("Ad", self.ad)
        self.add_row("Açılış Bakiyesi", self.acilis)
        self.add_row("", muted("Programa başladığınız andaki bakiye (ör. kasadaki nakit, bankadaki tutar). "
                               "Bütün günlerin devir bakiyelerini etkiler.", small=True))
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        b = button("Kaydet", "check", "primary", callback=self._save)
        b.setDefault(True)
        self.buttons.addWidget(b)
        self.finish_layout()

    def _save(self) -> None:
        try:
            if not self.acilis.is_valid():
                raise ServiceError("Açılış bakiyesi anlaşılamadı.")
            val = self.acilis.value() or 0
            old = self.pt["acilis_bakiye"] if self.pt else 0
            if val != old and self.svc.lock_date():
                if not ask(self, "Açılış Bakiyesi",
                           "Kapatılmış günler var. Kapalı günlerin raporları değişmez; yeni açılış bakiyesi ilk açık "
                           "günden itibaren devre yansır ve son kapalı günün gün sonu ile sonraki günün devri arasında "
                           "fark oluşur.\n\nBakiye düzeltmesi için çoğu zaman bir düzeltme kaydı (gelir/gider/virman) "
                           "girmek daha doğrudur. Yine de devam edilsin mi?"):
                    return
            if self.pt:
                self.svc.update_payment_type(self.pt["id"], self.ad.text(), val)
            else:
                self.svc.add_payment_type(self.ad.text(), val)
        except ServiceError as e:
            self.show_error(str(e))
            return
        self.ctx.bus.changed.emit("tanim")
        self.accept()


# =========================================================================== gün kapanışı
class CloseDayDialog(BaseDialog):
    def __init__(self, ctx, parent, rep: dict):
        self.ctx, self.svc, self.rep = ctx, ctx.svc, rep
        super().__init__(parent, f"Günü Kapat — {fmt_date(rep['tarih'], weekday=True)}",
                         "Kasada saydığınız tutarları 'Sayılan' sütununa yazın (boş bırakılabilir). "
                         "Fark otomatik hesaplanır.", 940)
        headers = ["Ödeme Tipi", "Devir", "Gelir", "Gider", "Virman", "Sistem Bakiyesi", "Sayılan", "Fark"]
        self.table = QTableWidget(len(rep["odeme"]), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(36)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setMinimumSectionSize(90)
        self.edits = {}
        for i, r in enumerate(rep["odeme"]):
            vals = [r["ad"], fmt_money(r["devir"]), fmt_money(r["gelir"]), fmt_money(r["gider"]),
                    fmt_money(r["virman_net"], plus=True), fmt_money(r["gun_sonu"])]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setTextAlignment((Qt.AlignLeft if j == 0 else Qt.AlignRight) | Qt.AlignVCenter)
                if j == 5:
                    f = QFont()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(i, j, it)
            e = MoneyEdit(allow_negative=True)
            e.setPlaceholderText(fmt_money(r["gun_sonu"]))
            e.setMinimumWidth(130)
            e.textChanged.connect(lambda _=None, row=i: self._diff(row))
            self.table.setCellWidget(i, 6, e)
            fark = QTableWidgetItem("-")
            fark.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 7, fark)
            self.edits[r["id"]] = (i, e, r["gun_sonu"])
        self.table.setFixedHeight(min(380, 44 + 36 * len(rep["odeme"])))
        self.v.addWidget(self.table)
        fill = button("Sayılanlara sistem bakiyelerini yaz", "copy", callback=self._fill_system)
        frow = QHBoxLayout()
        frow.addWidget(fill)
        frow.addStretch(1)
        self.v.addLayout(frow)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("Kapanış notu (isteğe bağlı) — ör. sayım farkının sebebi")
        self.note.setFixedHeight(64)
        self.v.addWidget(self.note)
        self.pdf = QCheckBox("Kapanıştan sonra günlük kasa raporunu (PDF) oluştur ve aç")
        self.pdf.setChecked(True)
        self.v.addWidget(self.pdf)
        self.v.addWidget(muted("Kapatılan güne ve önceki günlere kasa kaydı eklenemez ve değiştirilemez. "
                               "Gerektiğinde yönetici günü yeniden açabilir. Kapanışta otomatik yedek alınır.",
                               small=True))
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("İptal", callback=self.reject))
        self.ok_btn = button("Günü Kapat", "lock", "primary", callback=self._ok)
        self.buttons.addWidget(self.ok_btn)
        self.finish_layout()
        self.result_counts: dict = {}

    def _diff(self, row: int) -> None:
        for pid, (i, e, sys_val) in self.edits.items():
            if i != row:
                continue
            it = self.table.item(i, 7)
            v = e.value()
            if v is None or not e.text().strip():
                it.setText("-")
                it.setForeground(QColor(C["text"]))
            else:
                d = v - sys_val
                it.setText(fmt_money(d, plus=True) if d else "0,00")
                it.setForeground(QColor(C["gelir"] if d > 0 else (C["gider"] if d < 0 else C["text"])))

    def _fill_system(self) -> None:
        for pid, (i, e, sys_val) in self.edits.items():
            e.set_value(sys_val)

    def _ok(self) -> None:
        counts = {}
        for pid, (i, e, sys_val) in self.edits.items():
            if e.text().strip():
                if not e.is_valid():
                    self.show_error("Sayılan tutarlardan biri anlaşılamadı.")
                    return
                counts[pid] = e.value()
        diffs = [(self.table.item(i, 0).text(), counts[pid] - s) for pid, (i, e, s) in self.edits.items()
                 if pid in counts and counts[pid] != s]
        if diffs:
            text = "\n".join(f"• {n}: {fmt_money(d, plus=True)} ₺" for n, d in diffs)
            if not ask(self, "Sayım Farkı", f"Sayım farkı var:\n\n{text}\n\nYine de gün kapatılsın mı?",
                       yes="Kapat", danger=True):
                return
        self.result_counts = counts
        self.accept()


# =========================================================================== kurtarma anahtarı
class RecoveryKeyDialog(BaseDialog):
    def __init__(self, parent, key: str, reason: str = ""):
        super().__init__(parent, "Kurtarma Anahtarı",
                         reason or "Yönetici şifresini unutursanız giriş ekranındaki 'Şifremi unuttum' "
                                   "bağlantısıyla bu anahtarı kullanarak yeni şifre belirleyebilirsiniz. Verileriniz "
                                   "program şifreleriyle açılır; tüm şifreler unutulursa yalnızca bu anahtar açar. "
                                   "Anahtar yalnızca şimdi gösterilir.", 520)
        self.key = key
        lab = QLabel(key)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(20)
        f.setBold(True)
        lab.setFont(f)
        lab.setAlignment(Qt.AlignCenter)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lab.setStyleSheet(f"background: white; border: 1px dashed {C['border_strong']}; border-radius: 8px; "
                          f"padding: 14px; color: {C['navy']}; font-size: 20pt;")
        self.v.addWidget(lab)
        row = QHBoxLayout()
        row.addWidget(button("Kopyala", "copy", callback=lambda: QGuiApplication.clipboard().setText(key)))
        row.addWidget(button("Dosyaya kaydet…", "folder", callback=self._save_file))
        row.addStretch(1)
        self.v.addLayout(row)
        self.chk = QCheckBox("Anahtarı güvenli bir yere not ettim")
        self.v.addWidget(self.chk)
        self.buttons.addStretch(1)
        self.ok = button("Devam", "check", "primary", callback=self.accept)
        self.ok.setEnabled(False)
        self.chk.toggled.connect(self.ok.setEnabled)
        self.buttons.addWidget(self.ok)
        self.finish_layout()

    def _save_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Kurtarma anahtarını kaydet",
                                              str(Path.home() / "KasaTakip_kurtarma_anahtari.txt"),
                                              "Metin dosyası (*.txt)")
        if path:
            Path(path).write_text(f"Kasa Takip - Kurtarma Anahtarı\n\n{self.key}\n\n"
                                  "Giriş ekranında 'Şifremi unuttum' bağlantısında kullanılır.\n", encoding="utf-8")

    def reject(self):
        if self.chk.isChecked():
            super().reject()


# =========================================================================== yardım
HELP_HTML = """
<h2 style="color:#1E2A44">Kasa Takip — Hızlı Kullanım</h2>
<h3>Günlük akış</h3>
<ol>
<li><b>Günlük Kasa</b> ekranında tarihi seçin (varsayılan bugün).</li>
<li>Üstteki <b>hızlı kayıt</b> satırından ya da <b>F2 (Gelir)</b>, <b>F3 (Gider)</b>, <b>F4 (Virman)</b> ile kayıt girin.</li>
<li>Gün sonunda kasayı sayın, <b>Günü Kapat</b>'a basın, sayılan tutarı yazın. Program kasa raporunu (PDF) üretir ve yedek alır.</li>
</ol>
<h3>Kayıt türleri</h3>
<ul>
<li><b>Gelir / Gider</b>: kasaya giren / çıkan para. Cari seçilirse cari hesaba da işlenir (tahsilat / ödeme).</li>
<li><b>Virman</b>: ödeme tipleri arası aktarım (nakdin bankaya yatırılması, POS tutarının bankaya geçmesi). Gelir/gider değildir.</li>
<li><b>Cari Borç / Cari Alacak</b>: kasayı etkilemeyen fişler (veresiye satış, tedarikçi faturası, açılış bakiyesi).</li>
</ul>
<h3>Personel (yalnız yönetici)</h3>
<p>Her personelin cari gibi çalışan bir hesap kartı vardır. <b>(A)</b>: personele borçluyuz, <b>(B)</b>: personel bize borçlu.</p>
<ul>
<li><b>Hak Ediş</b> (maaş, prim…): kasayı etkilemez, personele borcumuz artar.</li>
<li><b>Ödeme</b>, <b>Avans</b>, <b>Borç Verme</b>: kasadan çıkar (gider); <b>Geri Ödeme</b>: kasaya girer (gelir).
Bu kayıtlar Günlük Kasa, Hareketler ve raporlarda da görünür; kapatılmış güne girilemez.</li>
<li>Ödemede <b>Hak edişi de kaydet (doğrudan ödeme)</b> seçilirse aynı tutarda hak ediş de oluşur; ödeme silinince o da silinir.</li>
<li><b>Toplu Maaş</b>: tüm aktif personel için hak ediş ve ödeme tek ekranda; <b>Ödenecekleri doldur</b> hak edişten
sonra kalan borcumuzu yazar. Kartın <b>PDF / Excel</b> düğmeleri Personel Hesap Dökümü raporunu üretir.</li>
</ul>
<h3>Kullanıcılar ve program sahibi</h3>
<p>İlk kurulumda açılan yönetici programın <b>sahibi</b>dir (Kullanıcılar ekranında "Sahip" yazar). Sahibin hesabını
başka bir yönetici pasif yapamaz, rolünü değiştiremez, şifresini sıfırlayamaz; sahip de kendini pasif yapamaz.
Yeni kurtarma anahtarını yalnızca sahip oluşturur. Sahip, <b>Sahipliği Devret</b> ile sahipliği başka bir aktif
yöneticiye verebilir. "Şifremi unuttum" ekranında kurtarma anahtarıyla her yöneticinin şifresi sıfırlanabilir.</p>
<h3>Kısayollar</h3>
<table cellpadding="4">
<tr><td><b>F2</b></td><td>Yeni gelir</td><td><b>F3</b></td><td>Yeni gider</td></tr>
<tr><td><b>F4</b></td><td>Virman</td><td><b>F5</b></td><td>Ekranı yenile</td></tr>
<tr><td><b>Enter</b></td><td>Seçili kaydı aç</td><td><b>Delete</b></td><td>Seçili kaydı sil</td></tr>
<tr><td><b>Ctrl+C</b></td><td>Seçili satırları Excel'e uygun kopyala</td><td><b>Ctrl+Shift+C</b></td><td>Başlıklarla kopyala</td></tr>
<tr><td><b>Ctrl+V</b></td><td>Excel'den yapıştırarak toplu kayıt</td><td><b>Ctrl+Enter</b></td><td>Kaydet ve yeni kayıt</td></tr>
<tr><td><b>Ctrl+←/→</b></td><td>Günlük Kasa'da önceki/sonraki gün</td><td><b>Ctrl+P</b></td><td>Günlük kasa PDF</td></tr>
</table>
<h3>Excel'den yapıştırma</h3>
<p>Excel'de satırları seçip <b>Ctrl+C</b> yapın; Hareketler veya Cariler ekranında <b>Excel'den Yapıştır</b>'a basın.
Sütunlar başlıklardan veya içerikten otomatik tanınır; gerekirse her sütunun ne olduğunu listeden değiştirebilirsiniz.
Hatalı satırlar kırmızı gösterilir ve kaydedilmez.</p>
<h3>Analiz</h3>
<p>Üstten <b>dönem</b>, <b>gruplama</b> (gün/hafta/ay) ve <b>karşılaştırma</b> (önceki eşit dönem veya geçen yılın
aynı dönemi) seçin. Sekmeler:</p>
<ul>
<li><b>Genel Bakış</b>: gelir, gider, net ve kâr marjı göstergeleri (değişim oranı ve küçük trend çizgisiyle),
gider/gelir oranı, tahsilat oranı, gün kapanış oranı ve sayım doğruluğu göstergeleri, gelir-gider grafiği,
kategori dağılımları ve <b>Öne Çıkanlar</b> (otomatik yorumlar).</li>
<li><b>Trend</b>: gelir, gider ve net çizgileri, kümülatif net, kategorilerin dönemlere göre dağılımı.</li>
<li><b>Kategoriler</b>: dağılım, karşılaştırma ve kategori × dönem tablosu (koyu hücre = yüksek tutar).</li>
<li><b>Kasa ve Ödeme Tipleri</b>: bakiye seyri, ödeme tipi özeti, kapanışlar ve sayım farkları.</li>
<li><b>Cariler</b>: alacak / borç yaşlandırması (0–30, 31–60, 61–90, 91–180, 180+ gün), en yüksek bakiyeler,
cari mizan.</li>
<li><b>Günler ve Saatler</b>: takvim ısı haritası, haftanın günleri, saatlere göre yoğunluk, en yüksek günler.</li>
<li><b>Yıllık Karşılaştırma</b>: seçilen yılın ayları önceki yılla yan yana.</li>
</ul>
<p>Grafiklerin üzerine gelince ayrıntı görünür; tablolarda satıra çift tıklayınca ilgili gün, cari veya kayıt açılır.
<b>PDF Rapor</b> düğmesi seçili dönemin kapsamlı analiz raporunu üretir.</p>
<h3>Raporlar</h3>
<p>Soldaki listeden raporu seçin, parametreleri (tarih, dönem, cari, ödeme tipi…) ayarlayın; rapor anında ekranda
önizlenir. <b>PDF Oluştur</b> raporu yazdırılabilir PDF olarak kaydedip açar, <b>Excel'e Aktar</b> tüm tabloları
Excel'e aktarır. Raporlar: Günlük Kasa, Kasa Defteri, Kapanışlar ve Sayım Farkları, Ödeme Tipi Bakiye,
Dönem Analiz, Hareket Dökümü, Kategori, Aylık Gelir-Gider Tablosu, Cari Ekstre, Cari Bakiye Listesi,
Cari Mizan, Alacak/Borç Yaşlandırma ve (yönetici için) İşlem Geçmişi ile Personel Hesap Dökümü. Oluşturulan dosyalar
Belgeler\\KasaTakip\\Raporlar klasörüne kaydedilir; son oluşturulanlar listeden tekrar açılabilir.</p>
<h3>Tüm defteri Excel'e almak</h3>
<p><b>Yedekleme → Tüm Verileri Excel'e Aktar</b>: bütün kayıtlar tek Excel dosyasında (Hareketler, Cariler,
Kategoriler, Ödeme Tipleri, Gün Kapanışları; her biri ayrı sayfa).<br>
<b>Raporlar → Kasa Defteri</b>, dönem <b>Tüm Zamanlar</b>, <b>Excel'e Aktar</b>: ilk kayıttan bugüne giriş, çıkış
ve yürüyen bakiyeli kasa defteri. Ödeme tipi seçilerek Nakit, Kart… defterleri ayrı ayrı da alınabilir.</p>
<h3>Yazdırılabilir kılavuz</h3>
<p>Aşağıdaki <b>Yazdırılabilir Kılavuz (PDF)</b> düğmesiyle bu bilgilerin yazdırılabilir halini alabilirsiniz.</p>
<h3>Yedekler</h3>
<p>Program her oturum açılışında, kapanışında ve gün kapanışında otomatik yedek alır
(Belgeler\\KasaTakip\\Yedekler). Yedekleme ekranından USB bellek veya bulut klasörüne ikinci bir kopya
tanımlayabilirsiniz.</p>
<h3>Verilerin şifrelenmesi</h3>
<p>Veritabanı ve tüm yedekler şifreli saklanır. Dosyalar, programdaki bir kullanıcının şifresi ya da
<b>kurtarma anahtarı</b> olmadan açılamaz; Windows hesabına şifre konmamış olsa bile. Şifrenizi unutursanız
giriş ekranındaki <b>Şifremi unuttum</b> ile kurtarma anahtarını kullanın. <b>Şifreler ve kurtarma anahtarı birlikte
kaybolursa verilere kimse ulaşamaz</b>: anahtarı yazdırıp güvenli bir yerde saklayın. Yeni bilgisayara geçerken son
yedeği geri yükleyin; yedeğin alındığı tarihteki şifre ya da kurtarma anahtarı sorulur. Kısa şifreler çalınan bir
dosyanın tahminle açılmasını kolaylaştırır; en az 8 karakterlik şifre kullanın. Program internete bağlanmaz.</p>
"""


class HelpDialog(BaseDialog):
    def __init__(self, parent=None, ctx=None):
        super().__init__(parent, "", "", 720)
        self.ctx = ctx
        self.setWindowTitle("Yardım")
        tb = QTextBrowser()
        tb.setHtml(HELP_HTML)
        tb.setMinimumHeight(520)
        self.v.addWidget(tb)
        self.v.addWidget(muted(f"{APP_TITLE} {__version__} · {APP_COPYRIGHT}", small=True))
        if ctx is not None:
            self.buttons.addWidget(button("Yazdırılabilir Kılavuz (PDF)", "print", callback=self._guide))
        self.buttons.addStretch(1)
        self.buttons.addWidget(button("Kapat", callback=self.accept))
        self.finish_layout()

    def _guide(self) -> None:
        from .. import reports_pdf
        from .context import save_report
        save_report(self.ctx, self, "", "Kasa_Takip_Kilavuz.pdf",
                    lambda p: reports_pdf.kilavuz_pdf(p, self.ctx.svc.firma(), self.ctx.user_label()))
