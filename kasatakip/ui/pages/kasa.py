"""Günlük Kasa: seçilen günün hareketleri, ödeme tipi bakiyeleri, hızlı kayıt, gün kapanışı ve PDF."""
from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)

from ...errors import ServiceError
from ...util import fmt_date, fmt_dt, fmt_money, today
from ..context import daily_pdf, delete_movements, duplicate_movement, edit_movement, new_movement, paste_import
from ..dialogs import CloseDayDialog
from ..theme import C, TUR_RENK
from ..widgets import (Banner, Card, Col, DataTable, DateNav, MoneyEdit, SearchCombo, Segmented, ask, button, combo,
                       combo_select, fit_table_height, muted, set_prop, warn)
from .base import Page


def _dash(v, r=None):
    return fmt_money(v) if v else "-"


def _plus(v, r=None):
    return fmt_money(v, plus=True) if v else "-"


class KasaPage(Page):
    title = "Günlük Kasa"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.rep = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # ------------------------------------------------ üst çubuk
        top = QHBoxLayout()
        top.setSpacing(8)
        self.nav = DateNav()
        top.addWidget(self.nav)
        self.badge = QLabel()
        top.addSpacing(6)
        top.addWidget(self.badge)
        top.addStretch(1)
        self.b_gelir = button("Gelir", "plus", "gelir", "Yeni gelir (F2)", lambda: self._new("GELIR"))
        self.b_gider = button("Gider", "minus", "gider", "Yeni gider (F3)", lambda: self._new("GIDER"))
        self.b_virman = button("Virman", "swap", "virman", "Ödeme tipleri arası aktarım (F4)",
                               lambda: self._new("VIRMAN"))
        self.b_cari = button("Cari Fişi", "receipt", "cari", "Kasayı etkilemeyen cari borç/alacak fişi",
                             lambda: self._new("CARI_BORC"))
        self.b_close = button("Günü Kapat", "lock", "primary", "Gün sonu sayım ve kapanış", self._close_day)
        self.b_open = button("Günü Aç", "unlock", None, "Kapanışı geri al (yalnız yönetici)", self._reopen)
        self.b_pdf = button("Kasa Raporu (PDF)", "pdf", None, "Günlük kasa raporu PDF (Ctrl+P)", self._pdf)
        for b in (self.b_close, self.b_open, self.b_pdf):
            top.addWidget(b)
        root.addLayout(top)

        self.lockbar = Banner()
        root.addWidget(self.lockbar)

        # ------------------------------------------------ hızlı kayıt
        qc = Card(margins=(14, 10, 14, 10), spacing=6)
        qh = QHBoxLayout()
        qh.setSpacing(8)
        self.q_tur = Segmented([("GELIR", "Gelir"), ("GIDER", "Gider")], tones={"GELIR": "GELIR", "GIDER": "GIDER"})
        self.q_tur.set_value("GELIR")
        self.q_kat = combo()
        self.q_kat.setMinimumWidth(160)
        self.q_odeme = combo()
        self.q_odeme.setMinimumWidth(100)
        self.q_tutar = MoneyEdit()
        self.q_tutar.setFixedWidth(130)
        self.q_cari = SearchCombo(placeholder="Cari (isteğe bağlı)")
        self.q_cari.setMinimumWidth(150)
        self.q_aciklama = QLineEdit()
        self.q_aciklama.setPlaceholderText("Açıklama")
        self.q_add = button("Ekle", "check", "primary", "Enter", self._quick_add)
        lab = QLabel("Hızlı kayıt")
        lab.setStyleSheet("font-weight: 700;")
        qh.addWidget(lab)
        for w in (self.q_tur, self.q_kat, self.q_odeme, self.q_tutar, self.q_cari):
            qh.addWidget(w)
        qh.addWidget(self.q_aciklama, 1)
        qh.addWidget(self.q_add)
        qc.add(qh)
        q2 = QHBoxLayout()
        q2.setSpacing(8)
        self.q_msg = muted("", small=True)
        q2.addWidget(self.q_msg, 1)
        q2.addWidget(muted("Ayrıntılı kayıt:", small=True, wrap=False))
        for b in (self.b_gelir, self.b_gider, self.b_virman, self.b_cari):
            q2.addWidget(b)
        qc.add(q2)
        root.addWidget(qc)
        self.q_tur.changed.connect(lambda _: self._fill_quick_categories())
        self.q_tutar.returnPressed.connect(self._quick_add)
        self.q_aciklama.returnPressed.connect(self._quick_add)

        # ------------------------------------------------ ödeme tipi bakiyeleri
        bc = Card("Ödeme Tipi Bakiyeleri", margins=(14, 10, 14, 12), spacing=6)
        self.bal_note = muted("", small=True, wrap=False)
        bc.header.addWidget(self.bal_note)
        self.bal = DataTable([
            Col("ad", "Ödeme Tipi", stretch=True, bold=True),
            Col("devir", "Devir", "money", stretch=True),
            Col("gelir", "Gelir", "money", stretch=True, fmt=_dash, color=lambda r: C["gelir"]),
            Col("gider", "Gider", "money", stretch=True, fmt=_dash, color=lambda r: C["gider"]),
            Col("virman_net", "Virman (±)", "smoney", stretch=True, fmt=_plus, color=lambda r: C["virman"]),
            Col("gun_sonu", "Gün Sonu", "smoney", stretch=True, bold=True),
            Col("sayilan", "Sayılan", "money", stretch=True, fmt=lambda v, r: fmt_money(v) if v is not None else "-"),
            Col("fark", "Fark", "smoney", stretch=True, bold=True,
                fmt=lambda v, r: ("0,00" if v == 0 else fmt_money(v, plus=True)) if v is not None else "-",
                color=lambda r: C["gelir"] if (r.get("fark") or 0) > 0 else None),
        ], sortable=False, multi=False, row_height=30)
        bc.add(self.bal)
        root.addWidget(bc)

        # ------------------------------------------------ hareketler + özet
        sp = QSplitter(Qt.Horizontal)
        sp.setChildrenCollapsible(False)
        left = Card("Günün Hareketleri", margins=(14, 10, 14, 10), spacing=8)
        left.header.addWidget(button("Excel'den Yapıştır", "paste", tip="Excel'den kopyalanan satırları ekle (Ctrl+V)",
                                     callback=lambda: self._paste(False)))
        self.table = DataTable([
            Col("saat", "Saat", width=66, align="c"),
            Col("tur_adi", "Tür", width=96, color=lambda r: TUR_RENK.get(r["tur"])),
            Col("kategori_gosterim", "Kategori", width=155),
            Col("odeme_gosterim", "Ödeme", width=120),
            Col("cari", "Cari", width=150),
            Col("tutar", "Tutar", "money", 112, bold=True, color=lambda r: TUR_RENK.get(r["tur"])),
            Col("aciklama", "Açıklama", stretch=True),
        ])
        self.table.setMinimumHeight(200)
        self.table.rowActivated.connect(lambda m: edit_movement(self.ctx, self, m))
        self.table.deleteRequested.connect(lambda: delete_movements(self.ctx, self, self.table.selected_rows()))
        self.table.pasteRequested.connect(lambda: self._paste(True))
        self.table.add_action("Düzenle", lambda: self._edit(), "edit", shortcut="Enter")
        self.table.add_action("Kopyasını oluştur (çoğalt)", self._dup, "copy")
        self.table.add_action("Sil", lambda: delete_movements(self.ctx, self, self.table.selected_rows()), "trash",
                              shortcut="Delete")
        self.table.add_action("Excel'den yapıştır…", lambda: self._paste(False), "paste", needs_selection=False,
                              shortcut="Ctrl+V", separator_before=True)
        left.add(self.table, 1)
        self.footer = QLabel()
        left.add(self.footer)
        sp.addWidget(left)

        right_inner = QWidget()
        rv = QVBoxLayout(right_inner)
        rv.setContentsMargins(0, 0, 4, 0)
        rv.setSpacing(12)
        sc = Card("Gün Özeti", margins=(14, 10, 14, 12), spacing=6)
        self.sum_grid = QGridLayout()
        self.sum_grid.setVerticalSpacing(6)
        self.sum_labels = {}
        for i, (key, text) in enumerate((("gelir", "Toplam gelir"), ("gider", "Toplam gider"), ("net", "Net"),
                                         ("adet", "Gelir / gider işlemi"), ("ort_gelir", "Ortalama gelir (işlem)"),
                                         ("ort_gider", "Ortalama gider (işlem)"), ("diger", "Virman / cari fişi"),
                                         ("kapanis", "Kapanış"))):
            lb = QLabel(text)
            lb.setProperty("muted", True)
            val = QLabel()
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setStyleSheet("font-weight: 700;")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.sum_grid.addWidget(lb, i, 0)
            self.sum_grid.addWidget(val, i, 1)
            self.sum_labels[key] = val
        sc.add(self.sum_grid)
        rv.addWidget(sc)
        self.cat_card = Card("Kategoriler", margins=(14, 10, 14, 12), spacing=6)
        self.cat_g = DataTable([Col("ad", "Gelir kategorisi", stretch=True), Col("adet", "Adet", "int", 50),
                                Col("toplam", "Tutar", "money", 100, color=lambda r: C["gelir"])],
                               sortable=False, row_height=28)
        self.cat_c = DataTable([Col("ad", "Gider kategorisi", stretch=True), Col("adet", "Adet", "int", 50),
                                Col("toplam", "Tutar", "money", 100, color=lambda r: C["gider"])],
                               sortable=False, row_height=28)
        self.cat_card.add(self.cat_g)
        self.cat_card.add(self.cat_c)
        rv.addWidget(self.cat_card)
        rv.addStretch(1)
        rs = QScrollArea()
        rs.setWidgetResizable(True)
        rs.setWidget(right_inner)
        rs.setMinimumWidth(330)
        sp.addWidget(rs)
        sp.setStretchFactor(0, 3)
        sp.setStretchFactor(1, 1)
        sp.setSizes([900, 360])
        root.addWidget(sp, 1)

        self.nav.changed.connect(lambda _: self.safe_refresh())
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=lambda: self.nav.shift(-1),
                  context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self.nav.shift(1),
                  context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self._pdf, context=Qt.WidgetWithChildrenShortcut)
        self._lists_loaded = False

    # ------------------------------------------------------------ yardımcılar
    def default_date(self):
        return self.nav.date()

    def set_date(self, d: dt.date) -> None:
        self.nav.set_date(d)

    def page_subtitle(self) -> str:
        return "Seçilen günün kasa hareketleri, ödeme tipi bakiyeleri ve gün sonu kapanışı"

    def _fill_quick_lists(self) -> None:
        cur_pt = self.q_odeme.currentData()
        self.q_odeme.clear()
        for p in self.svc.payment_types(include_inactive=False):
            self.q_odeme.addItem(p["ad"], p["id"])
        if cur_pt:
            combo_select(self.q_odeme, cur_pt)
        self.q_cari.set_items([(c["unvan"], c["id"]) for c in self.svc.caris()], none_text="")
        self._fill_quick_categories()

    def _fill_quick_categories(self) -> None:
        cur = self.q_kat.currentData()
        self.q_kat.clear()
        for k in self.svc.categories(self.q_tur.value() or "GELIR", include_inactive=False):
            self.q_kat.addItem(k["ad"], k["id"])
        combo_select(self.q_kat, cur)

    # ------------------------------------------------------------ yenile
    def refresh(self) -> None:
        d = self.nav.date()
        self._fill_quick_lists()
        rep = self.svc.day_report(d)
        self.rep = rep
        locked = rep["kilitli"]
        future = d > today()
        if rep["kapali"]:
            k = rep["kapanis"]
            self.badge.setText("KAPALI")
            self.badge.setToolTip(f"Kapanış: {fmt_dt(k['kapanis_zamani'])} · {k.get('kapatan') or ''}")
            set_prop(self.badge, "badge", "kapali")
        elif locked:
            self.badge.setText("KİLİTLİ")
            set_prop(self.badge, "badge", "kilitli")
        else:
            self.badge.setText("AÇIK")
            set_prop(self.badge, "badge", "acik")
        if locked:
            msg = (f"Bu gün kapatılmış ({fmt_dt(rep['kapanis']['kapanis_zamani'])}, "
                   f"{rep['kapanis'].get('kapatan') or ''})." if rep["kapali"] else
                   f"Bu gün, sonraki bir günün kapanışıyla kilitlendi (son kapanış: {fmt_date(rep['son_kapanis'])}).")
            msg += " Kasa kayıtları değiştirilemez; cari fişleri eklenebilir."
            if self.svc.is_admin:
                msg += " Değişiklik gerekirse 'Günü Aç' ile kapanışı geri alabilirsiniz."
            self.lockbar.show_message(msg)
        elif future:
            self.lockbar.show_message("İleri bir tarih seçili. Kayıt girebilirsiniz, ancak gün kapatılamaz.")
        else:
            self.lockbar.hide()
        for w in (self.b_gelir, self.b_gider, self.b_virman, self.q_add, self.q_tur, self.q_kat, self.q_odeme,
                  self.q_tutar, self.q_cari, self.q_aciklama):
            w.setEnabled(not locked)
        self.b_close.setVisible(not locked)
        self.b_close.setEnabled(not future)
        self.b_open.setVisible(locked)
        self.b_open.setEnabled(self.svc.is_admin)
        if not self.svc.is_admin:
            self.b_open.setToolTip("Günü yalnızca yönetici yeniden açabilir")

        # bakiyeler
        self.bal.set_rows(rep["odeme"])
        self.bal.setColumnHidden(6, not rep["kapali"])
        self.bal.setColumnHidden(7, not rep["kapali"])
        fit_table_height(self.bal, len(rep["odeme"]))
        tot_dev = sum(r["devir"] for r in rep["odeme"])
        tot_end = sum(r["gun_sonu"] for r in rep["odeme"])
        self.bal_note.setText(f"Toplam devir {fmt_money(tot_dev)} ₺  →  toplam gün sonu {fmt_money(tot_end)} ₺")

        # hareketler
        self.table.set_rows(rep["hareketler"])
        net_color = C["gider"] if rep["net"] < 0 else C["text"]
        self.footer.setText(
            f"<span style='color:{C['muted']}'>{len(rep['hareketler'])} kayıt</span> &nbsp;·&nbsp; "
            f"Gelir <b style='color:{C['gelir']}'>{fmt_money(rep['gelir'])}</b> &nbsp;·&nbsp; "
            f"Gider <b style='color:{C['gider']}'>{fmt_money(rep['gider'])}</b> &nbsp;·&nbsp; "
            f"Net <b style='color:{net_color}'>{fmt_money(rep['net'])} ₺</b>")

        s = self.sum_labels
        s["gelir"].setText(fmt_money(rep["gelir"], True))
        s["gelir"].setStyleSheet(f"font-weight: 700; color: {C['gelir']};")
        s["gider"].setText(fmt_money(rep["gider"], True))
        s["gider"].setStyleSheet(f"font-weight: 700; color: {C['gider']};")
        s["net"].setText(fmt_money(rep["net"], True))
        s["adet"].setText(f"{rep['gelir_adet']} / {rep['gider_adet']}")
        s["ort_gelir"].setText(fmt_money(rep["ort_gelir"], True))
        s["ort_gider"].setText(fmt_money(rep["ort_gider"], True))
        s["diger"].setText(f"{rep['virman_adet']} / {rep['cari_fis_adet']}")
        s["kapanis"].setText(f"{fmt_dt(rep['kapanis']['kapanis_zamani'])}" if rep["kapali"] else
                             ("Kilitli" if locked else "Açık"))
        g = [r for r in rep["kat_gelir"] if r["adet"]]
        c = [r for r in rep["kat_gider"] if r["adet"]]
        self.cat_g.set_rows(g or [{"ad": "Gelir kaydı yok", "adet": None, "toplam": None, "_muted": True}])
        self.cat_c.set_rows(c or [{"ad": "Gider kaydı yok", "adet": None, "toplam": None, "_muted": True}])
        fit_table_height(self.cat_g, len(g))
        fit_table_height(self.cat_c, len(c))

    # ------------------------------------------------------------ işlemler
    def _new(self, tur: str) -> None:
        new_movement(self.ctx, self, tur, {"tarih": self.nav.date()})

    def _edit(self) -> None:
        r = self.table.current_row()
        if r:
            edit_movement(self.ctx, self, r)

    def _dup(self) -> None:
        r = self.table.current_row()
        if r:
            d = self.nav.date()
            duplicate_movement(self.ctx, self, r, d if not self.svc.is_locked(d) else today())

    def _paste(self, from_clipboard: bool) -> None:
        paste_import(self.ctx, self, "hareket", default_date=self.nav.date(), from_clipboard=from_clipboard)

    def _quick_add(self) -> None:
        if not self.q_add.isEnabled():
            return
        cid = self.q_cari.current_id()
        try:
            if cid is SearchCombo.INVALID:
                raise ServiceError(f"'{self.q_cari.currentText()}' adında cari yok. Listeden seçin veya Cariler "
                                   "ekranından ekleyin.")
            if not self.q_tutar.is_valid():
                raise ServiceError("Tutar anlaşılamadı. Örnek: 1.250,50")
            data = {"tur": self.q_tur.value(), "tarih": self.nav.date(), "kategori_id": self.q_kat.currentData(),
                    "odeme_tipi_id": self.q_odeme.currentData(), "cari_id": cid, "tutar": self.q_tutar.value(),
                    "aciklama": self.q_aciklama.text()}
            mid = self.svc.add_movement(data)
        except ServiceError as e:
            self.q_msg.setText(f"<span style='color:{C['gider']}; font-weight:600'>{e}</span>")
            if "Tutar" in str(e):
                self.q_tutar.set_invalid(True)
                self.q_tutar.setFocus()
            return
        m = self.svc.get_movement(mid)
        self.q_msg.setText(f"<span style='color:{C['gelir']}; font-weight:600'>✓ Eklendi:</span> "
                           f"{m['tur_adi']} · {m['kategori_gosterim']} · {m['odeme_gosterim']} · "
                           f"{fmt_money(m['tutar'])} ₺")
        self.q_tutar.clear()
        self.q_aciklama.clear()
        self.q_cari.set_current_id(None)
        self.ctx.bus.changed.emit("hareket")
        self.table.select_where("id", mid)
        self.q_kat.setFocus()

    def _close_day(self) -> None:
        d = self.nav.date()
        rep = self.svc.day_report(d)
        unclosed = [x for x in self.svc.unclosed_days(400) if x < d.isoformat()]
        if unclosed:
            days = ", ".join(fmt_date(x) for x in unclosed[-6:])
            if not ask(self, "Önceki Günler", f"Bu günden önce kapatılmamış günler var: {days}"
                                             f"{' …' if len(unclosed) > 6 else ''}\n\nBu günü kapatırsanız o günler de "
                                             "kilitlenir (ayrı kapanış kaydı ve sayım olmadan). Devam edilsin mi?",
                       yes="Devam"):
                return
        dlg = CloseDayDialog(self.ctx, self, rep)
        if not dlg.exec():
            return
        counts, note, want_pdf = dlg.result_counts, dlg.note.toPlainText(), dlg.pdf.isChecked()
        try:
            self.svc.close_day(d, counts, note)
        except ServiceError as e:
            warn(self, "Gün kapatılamadı", str(e))
            return
        try:
            self.ctx.backups.backup("gunsonu")
        except Exception as e:  # noqa: BLE001
            self.ctx.status(f"Gün sonu yedeği alınamadı: {e}")
        self.ctx.bus.changed.emit("kapanis")
        self.ctx.status(f"{fmt_date(d)} kasası kapatıldı ve yedeklendi.")
        if want_pdf:
            daily_pdf(self.ctx, self, d)

    def _reopen(self) -> None:
        d = self.nav.date()
        days = self.svc.closings_from(d)
        if not days:
            return
        text = ("Şu günlerin kapanışı geri alınacak:\n\n" + ", ".join(fmt_date(x) for x in days) +
                "\n\nBu günlerdeki kasa kayıtları yeniden değiştirilebilir hale gelir. İşlem geçmişine kaydedilir.")
        if not ask(self, "Günü Aç", text, yes="Kapanışı Geri Al", danger=True):
            return
        try:
            self.svc.reopen_from(d)
        except ServiceError as e:
            warn(self, "Gün açılamadı", str(e))
            return
        self.ctx.bus.changed.emit("kapanis")
        self.ctx.status("Kapanış geri alındı.")

    def _pdf(self) -> None:
        daily_pdf(self.ctx, self, self.nav.date())
