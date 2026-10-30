"""Personel (yalnız yönetici): personel listesi, bakiyeler, hesap kartı ve hesap dökümü."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSplitter, QVBoxLayout

from ...errors import ServiceError
from ...util import fmt_balance, fmt_date, fmt_money, today
from ..context import edit_movement, export_report_excel, report_pdf
from ..personel_dialogs import PersonelDialog, PersonelKayitDialog, TopluMaasDialog, bal_color
from ..theme import C, TUR_RENK
from ..widgets import Card, Col, DataTable, PeriodSelector, Segmented, ask, button, muted, warn
from .base import Page


def _bal_color(r):
    return bal_color(r.get("bakiye"))


class PersonelPage(Page):
    title = "Personel"
    subtitle = "Personel hesapları · Borç (B): personel bize borçlu · Alacak (A): personele borçluyuz"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.cur: dict | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        sp = QSplitter(Qt.Horizontal)
        sp.setChildrenCollapsible(False)
        root.addWidget(sp)

        # ------------------------------------------------ sol: liste
        left = Card(margins=(14, 12, 14, 10), spacing=8)
        tb = QHBoxLayout()
        tb.setSpacing(6)
        tb.addWidget(button("Yeni Personel", "plus", "primary", callback=self._new))
        tb.addWidget(button("Toplu Maaş", "list", tip="Maaş günü: hak edişleri ve ödemeleri tek ekranda girin",
                            callback=self._toplu))
        tb.addStretch(1)
        left.add(tb)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ad soyad, kod, telefon, görev ara…")
        self.search.setClearButtonEnabled(True)
        left.add(self.search)
        f = QHBoxLayout()
        self.durum = Segmented([("aktif", "Aktif"), ("pasif", "Pasif"), ("tumu", "Tümü")])
        self.durum.set_value("aktif")
        f.addWidget(self.durum)
        f.addStretch(1)
        left.add(f)
        self.list = DataTable([
            Col("kod", "Kod", width=62),
            Col("ad_soyad", "Ad Soyad", stretch=True),
            Col("gorev", "Görev", width=90),
            Col("bakiye", "Bakiye", "balance", 116, bold=True, color=_bal_color),
            Col("bu_ay_odenen", "Bu Ay Ödenen", "money", 98, fmt=lambda v, r: fmt_money(v) if v else ""),
            Col("son_kayit", "Son Kayıt", "date", 86),
        ], multi=False)
        self.list.rowActivated.connect(lambda r: self._edit())
        self.list.selectionChangedRows.connect(self._selected)
        self.list.add_action("Bilgileri düzenle", self._edit, "edit")
        self.list.add_action("Kayıt gir", self._kayit, "receipt")
        left.add(self.list, 1)
        self.totals = QLabel()
        self.totals.setWordWrap(True)
        left.add(self.totals)
        left.setMinimumWidth(460)
        sp.addWidget(left)

        # ------------------------------------------------ sağ: kart + döküm
        right = Card(margins=(16, 14, 16, 12), spacing=10)
        hdr = QHBoxLayout()
        nv = QVBoxLayout()
        self.name = QLabel("Personel seçin")
        self.name.setObjectName("PageTitle")
        self.name.setStyleSheet("font-size: 17pt;")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info = muted("Soldaki listeden bir personel seçin.")
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        nv.addWidget(self.name)
        nv.addWidget(self.info)
        hdr.addLayout(nv, 1)
        bv = QVBoxLayout()
        self.bal = QLabel("")
        self.bal.setObjectName("BigValue")
        self.bal.setAlignment(Qt.AlignRight)
        self.bal.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.bal_word = muted("", small=True)
        self.bal_word.setAlignment(Qt.AlignRight)
        bv.addWidget(self.bal)
        bv.addWidget(self.bal_word)
        hdr.addLayout(bv)
        right.add(hdr)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.b_kayit = button("Kayıt Gir", "receipt", "cari", "Hak ediş, ödeme, avans, borç verme, geri ödeme",
                              self._kayit)
        self.b_edit = button("Düzenle", "edit", callback=self._edit)
        self.b_aktif = button("Pasif Yap", "lock", callback=self._toggle)
        self.b_del = button("Sil", "trash", "danger", callback=self._delete)
        for b in (self.b_kayit, self.b_edit, self.b_aktif, self.b_del):
            acts.addWidget(b)
        acts.addStretch(1)
        right.add(acts)
        er = QHBoxLayout()
        lab = QLabel("Döküm dönemi")
        lab.setStyleSheet("font-weight: 700;")
        er.addWidget(lab)
        self.period = PeriodSelector("Bu Yıl")
        er.addWidget(self.period)
        er.addStretch(1)
        right.add(er)
        self.b_pdf = button("PDF", "pdf", "primary", callback=self._pdf)
        self.b_xls = button("Excel", "excel", callback=self._excel)
        self.ekstre = DataTable([
            Col("tarih", "Tarih", "date", 88),
            Col("islem", "İşlem", width=100, color=lambda r: TUR_RENK.get(r.get("tur"))),
            Col("kalem", "Kalem", width=100),
            Col("odeme", "Ödeme Tipi", width=82),
            Col("borc", "Borç", "money", 92, fmt=lambda v, r: fmt_money(v) if v else ""),
            Col("alacak", "Alacak", "money", 92, fmt=lambda v, r: fmt_money(v) if v else ""),
            Col("bakiye", "Bakiye", "balance", 112, bold=True, color=_bal_color),
            Col("aciklama", "Açıklama", stretch=True),
            Col("olusturan", "Giren", width=72),
        ], sortable=False)
        self.ekstre.rowActivated.connect(self._open_row)
        self.ekstre.add_action("Kaydı aç", lambda: self._open_row(self.ekstre.current_row()), "edit")
        right.add(self.ekstre, 1)
        fr = QHBoxLayout()
        self.ek_footer = QLabel()
        self.ek_footer.setWordWrap(True)
        fr.addWidget(self.ek_footer, 1)
        fr.addWidget(self.b_pdf)
        fr.addWidget(self.b_xls)
        right.add(fr)
        right.setMinimumWidth(620)
        sp.addWidget(right)
        sp.setStretchFactor(0, 2)
        sp.setStretchFactor(1, 3)
        sp.setSizes([480, 800])

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._reload_list)
        self.search.textChanged.connect(lambda _: self._timer.start())
        self.durum.changed.connect(lambda _: self._reload_list())
        self.period.changed.connect(self._load_ekstre)
        self._set_detail_enabled(False)

    # ------------------------------------------------------------ yenile
    def refresh(self) -> None:
        self._reload_list()

    def _reload_list(self) -> None:
        d = self.durum.value() or "aktif"
        rows = self.svc.personel_listesi(search=self.search.text().strip(), include_inactive=d != "aktif")
        if d == "pasif":
            rows = [r for r in rows if not r["aktif"]]
        for r in rows:
            if not r["aktif"]:
                r["_muted"] = True
        keep = self.cur["id"] if self.cur else None
        self.list.set_rows(rows)
        if keep:
            self.list.select_where("id", keep)
        borcumuz = -sum(r["bakiye"] for r in rows if r["bakiye"] < 0)
        alacak = sum(r["bakiye"] for r in rows if r["bakiye"] > 0)
        self.totals.setText(
            f"<span style='color:{C['muted']}'>{sum(1 for r in rows if r['aktif'])} aktif personel</span>"
            f" &nbsp;·&nbsp; Personele borcumuz <b style='color:{C['gider']}'>{fmt_money(borcumuz)} ₺</b>"
            f" &nbsp;·&nbsp; Personelin bize borcu <b style='color:{C['gelir']}'>{fmt_money(alacak)} ₺</b>")
        if not self.list.selected_rows():
            self.cur = None
            self._show_detail()

    def _selected(self) -> None:
        self.cur = self.list.current_row()
        self._show_detail()

    def _set_detail_enabled(self, on: bool) -> None:
        for w in (self.b_kayit, self.b_edit, self.b_aktif, self.b_del, self.b_pdf, self.b_xls):
            w.setEnabled(on)

    def _show_detail(self) -> None:
        if not self.cur:
            self.name.setText("Personel seçin")
            self.info.setText("Soldaki listeden bir personel seçin ya da 'Yeni Personel' ile ekleyin.")
            self.bal.setText("")
            self.bal_word.setText("")
            self.ekstre.set_rows([])
            self.ek_footer.setText("")
            self._set_detail_enabled(False)
            return
        try:
            p = self.svc.get_personel(self.cur["id"])
        except ServiceError:
            self.cur = None
            self._show_detail()
            return
        self.cur = p
        self._set_detail_enabled(True)
        self.b_aktif.setText("Pasif Yap" if p["aktif"] else "Aktif Yap")
        self.name.setText(p["ad_soyad"] + ("  (pasif)" if not p["aktif"] else ""))
        bits = [p["kod"], p.get("gorev"), p.get("telefon"), f"IBAN: {p['iban']}" if p.get("iban") else "",
                f"İşe giriş: {fmt_date(p['ise_giris'])}" if p.get("ise_giris") else ""]
        self.info.setText("  ·  ".join(b for b in bits if b))
        self.bal.setText(fmt_balance(p["bakiye"], True))
        self.bal.setStyleSheet(f"color: {_bal_color(p)};")
        self.bal_word.setText(p["durum"])
        self._load_ekstre()

    def _load_ekstre(self) -> None:
        if not self.cur:
            return
        s, e = self.svc.span(*self.period.range())
        st = self.svc.personel_ekstre(self.cur["id"], s, e)
        rows = [{"kaynak": "", "id": None, "tarih": s.isoformat(), "islem": "Devir", "aciklama": "Önceki dönemden "
                 "devreden", "borc": 0, "alacak": 0, "bakiye": st["devir"], "_bold": True}] + st["satirlar"]
        self.ekstre.set_rows(rows, keep_selection_key=None)
        self.ek_footer.setText(
            f"<span style='color:{C['muted']}'>{len(st['satirlar'])} kayıt</span> &nbsp;·&nbsp; "
            f"Dönem borç <b>{fmt_money(st['toplam_borc'])}</b> &nbsp;·&nbsp; "
            f"Dönem alacak <b>{fmt_money(st['toplam_alacak'])}</b> &nbsp;·&nbsp; "
            f"Dönem sonu bakiye <b style='color:{_bal_color(st)}'>{fmt_balance(st['bakiye'])}</b>")

    # ------------------------------------------------------------ işlemler
    def _new(self) -> None:
        dlg = PersonelDialog(self.ctx, self)
        if dlg.exec() and dlg.saved_id:
            self.cur = {"id": dlg.saved_id}
            self._reload_list()
            self.list.select_where("id", dlg.saved_id)

    def _edit(self) -> None:
        if self.cur:
            PersonelDialog(self.ctx, self, personel=self.svc.get_personel(self.cur["id"])).exec()

    def _kayit(self) -> None:
        PersonelKayitDialog(self.ctx, self, personel_id=self.cur["id"] if self.cur else None).exec()

    def _toplu(self) -> None:
        dlg = TopluMaasDialog(self.ctx, self)
        if dlg.exec() and dlg.result_counts:
            r = dlg.result_counts
            self.ctx.status(f"Toplu kayıt: {r['kisi']} kişi, {r['hak_edis']} hak ediş, {r['odeme']} ödeme.")

    def _toggle(self) -> None:
        if not self.cur:
            return
        p = self.svc.get_personel(self.cur["id"])
        self.svc.set_personel_active(p["id"], not p["aktif"])
        self.ctx.bus.changed.emit("personel")

    def _delete(self) -> None:
        if not self.cur:
            return
        p = self.svc.get_personel(self.cur["id"])
        if not ask(self, "Personel Sil", f"'{p['ad_soyad']}' silinsin mi?", yes="Sil", danger=True):
            return
        try:
            self.svc.delete_personel(p["id"])
        except ServiceError as e:
            warn(self, "Silinemedi", str(e))
            return
        self.cur = None
        self.ctx.bus.changed.emit("personel")

    def _open_row(self, r) -> None:
        if not r or not r.get("id"):
            return
        if r.get("kaynak") == "hareket":
            edit_movement(self.ctx, self, r)
        elif r.get("kaynak") == "fis":
            try:
                fis = self.svc.get_personel_fisi(r["id"])
            except ServiceError as e:
                warn(self, "Kayıt", str(e))
                self.ctx.bus.changed.emit("personel")
                return
            PersonelKayitDialog(self.ctx, self, fis=fis).exec()

    def _values(self) -> dict:
        s, e = self.period.range()
        return {"personel": self.cur["id"], "donem": (s, e)}

    def _pdf(self) -> None:
        if self.cur:
            report_pdf(self.ctx, self, "personel_ekstre", self._values())

    def _excel(self) -> None:
        if not self.cur:
            return
        from ... import reports
        try:
            rep = reports.build("personel_ekstre", self.svc, self._values())
        except ServiceError as e:
            warn(self, "Rapor", str(e))
            return
        export_report_excel(self.ctx, self, rep)
