"""Cariler: müşteri/tedarikçi listesi, bakiyeler, ekstre, tahsilat/ödeme ve borç/alacak fişleri."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QSplitter, QVBoxLayout

from ... import reports_pdf
from ...errors import ServiceError
from ...util import balance_word, fmt_balance, fmt_date, fmt_money, today
from ..context import edit_movement, export_excel, new_movement, paste_import, safe_name, save_report
from ..dialogs import CariDialog
from ..theme import C, TUR_RENK
from ..widgets import (Card, Col, DataTable, PeriodSelector, Segmented, ask, button, muted, warn)
from .base import Page


def _bal_color(r):
    b = r.get("bakiye") or 0
    return C["gelir"] if b > 0 else (C["gider"] if b < 0 else C["muted"])


class CarilerPage(Page):
    title = "Cariler"
    subtitle = "Müşteri ve tedarikçi hesapları · Borç (B): cari bize borçlu · Alacak (A): biz cariye borçluyuz"

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
        tb.addWidget(button("Yeni Cari", "plus", "primary", callback=self._new))
        self.b_edit = button("Düzenle", "edit", callback=self._edit_cari)
        self.b_del = button("Sil", "trash", "danger", callback=self._delete)
        tb.addWidget(self.b_edit)
        tb.addWidget(self.b_del)
        tb.addStretch(1)
        tb.addWidget(button("", "paste", tip="Excel'den cari listesi yapıştır",
                            callback=lambda: paste_import(self.ctx, self, "cari")))
        tb.addWidget(button("", "excel", tip="Cari listesini Excel'e aktar", callback=self._export_list))
        tb.addWidget(button("", "pdf", tip="Cari bakiye listesi (PDF)", callback=self._list_pdf))
        left.add(tb)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Ünvan, kod, telefon, vergi no ara…")
        self.search.setClearButtonEnabled(True)
        left.add(self.search)
        f = QHBoxLayout()
        self.tip = Segmented([("", "Tümü"), ("MUSTERI", "Müşteriler"), ("TEDARIKCI", "Tedarikçiler")])
        self.tip.set_value("")
        f.addWidget(self.tip)
        f.addStretch(1)
        left.add(f)
        f2 = QHBoxLayout()
        self.only_bal = QCheckBox("Sadece bakiyesi olanlar")
        self.inactive = QCheckBox("Pasifleri göster")
        f2.addWidget(self.only_bal)
        f2.addWidget(self.inactive)
        f2.addStretch(1)
        left.add(f2)
        self.list = DataTable([
            Col("kod", "Kod", width=66),
            Col("unvan", "Ünvan", stretch=True),
            Col("tip_adi", "Tip", width=88),
            Col("bakiye", "Bakiye", "balance", 132, bold=True, color=_bal_color),
        ], multi=False)
        self.list.rowActivated.connect(lambda r: self._edit_cari())
        self.list.selectionChangedRows.connect(self._selected)
        self.list.add_action("Bilgileri düzenle", self._edit_cari, "edit")
        self.list.add_action("Tahsilat al", lambda: self._mov("GELIR"), "in")
        self.list.add_action("Ödeme yap", lambda: self._mov("GIDER"), "out")
        self.list.add_action("Borç fişi", lambda: self._mov("CARI_BORC"), "receipt")
        self.list.add_action("Alacak fişi", lambda: self._mov("CARI_ALACAK"), "receipt")
        self.list.add_action("Ekstre PDF", self._ekstre_pdf, "pdf")
        left.add(self.list, 1)
        self.totals = QLabel()
        self.totals.setWordWrap(True)
        left.add(self.totals)
        left.setMinimumWidth(420)
        sp.addWidget(left)

        # ------------------------------------------------ sağ: detay + ekstre
        right = Card(margins=(16, 14, 16, 12), spacing=10)
        hdr = QHBoxLayout()
        nv = QVBoxLayout()
        self.name = QLabel("Cari seçin")
        self.name.setObjectName("PageTitle")
        self.name.setStyleSheet("font-size: 17pt;")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info = muted("Soldaki listeden bir cari seçin.")
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
        self.b_tah = button("Tahsilat Al", "in", "gelir", "Cariden para girişi (kasaya gelir, carinin alacağına)",
                            lambda: self._mov("GELIR"))
        self.b_ode = button("Ödeme Yap", "out", "gider", "Cariye para çıkışı (kasadan gider, carinin borcuna)",
                            lambda: self._mov("GIDER"))
        self.b_borc = button("Borç Fişi", "receipt", "cari", "Kasayı etkilemez: ör. müşteriye veresiye satış",
                             lambda: self._mov("CARI_BORC"))
        self.b_alacak = button("Alacak Fişi", "receipt", "cari", "Kasayı etkilemez: ör. tedarikçi faturası",
                               lambda: self._mov("CARI_ALACAK"))
        for b in (self.b_tah, self.b_ode, self.b_borc, self.b_alacak):
            acts.addWidget(b)
        acts.addStretch(1)
        right.add(acts)
        er = QHBoxLayout()
        lab = QLabel("Ekstre dönemi")
        lab.setStyleSheet("font-weight: 700;")
        er.addWidget(lab)
        self.period = PeriodSelector("Bu Yıl")
        er.addWidget(self.period)
        er.addStretch(1)
        right.add(er)
        self.b_pdf = button("Ekstre PDF", "pdf", "primary", callback=self._ekstre_pdf)
        self.b_xls = button("Excel", "excel", callback=self._ekstre_excel)
        self.ekstre = DataTable([
            Col("tarih", "Tarih", "date", 104),
            Col("islem", "İşlem", width=132, color=lambda r: TUR_RENK.get(r.get("tur"))),
            Col("aciklama", "Açıklama", stretch=True),
            Col("borc", "Borç", "money", 105, fmt=lambda v, r: fmt_money(v) if v else ""),
            Col("alacak", "Alacak", "money", 105, fmt=lambda v, r: fmt_money(v) if v else ""),
            Col("bakiye", "Bakiye", "balance", 128, bold=True, color=_bal_color),
        ], sortable=False)
        self.ekstre.rowActivated.connect(self._open_movement)
        self.ekstre.add_action("Kaydı aç", lambda: self._open_movement(self.ekstre.current_row()), "edit")
        right.add(self.ekstre, 1)
        fr = QHBoxLayout()
        self.ek_footer = QLabel()
        self.ek_footer.setWordWrap(True)
        fr.addWidget(self.ek_footer, 1)
        fr.addWidget(self.b_pdf)
        fr.addWidget(self.b_xls)
        right.add(fr)
        right.setMinimumWidth(560)
        sp.addWidget(right)
        sp.setStretchFactor(0, 2)
        sp.setStretchFactor(1, 3)
        sp.setSizes([470, 700])

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._reload_list)
        self.search.textChanged.connect(lambda _: self._timer.start())
        self.tip.changed.connect(lambda _: self._reload_list())
        self.only_bal.toggled.connect(lambda _: self._reload_list())
        self.inactive.toggled.connect(lambda _: self._reload_list())
        self.period.changed.connect(self._load_ekstre)
        self._set_detail_enabled(False)

    # ------------------------------------------------------------ yenile
    def refresh(self) -> None:
        self._reload_list()

    def _reload_list(self) -> None:
        rows = self.svc.caris(search=self.search.text().strip(), tip=self.tip.value() or None,
                              include_inactive=self.inactive.isChecked(), only_balance=self.only_bal.isChecked())
        for r in rows:
            if not r["aktif"]:
                r["_muted"] = True
        keep = self.cur["id"] if self.cur else None
        self.list.set_rows(rows)
        if keep:
            self.list.select_where("id", keep)
        alacak = sum(r["bakiye"] for r in rows if r["bakiye"] > 0)
        borc = -sum(r["bakiye"] for r in rows if r["bakiye"] < 0)
        self.totals.setText(
            f"<span style='color:{C['muted']}'>{len(rows)} cari</span> &nbsp;·&nbsp; "
            f"Alacaklarımız <b style='color:{C['gelir']}'>{fmt_money(alacak)} ₺</b> &nbsp;·&nbsp; "
            f"Borçlarımız <b style='color:{C['gider']}'>{fmt_money(borc)} ₺</b>")
        if not self.list.selected_rows():
            self.cur = None
            self._show_detail()

    def _selected(self) -> None:
        r = self.list.current_row()
        self.cur = r
        self._show_detail()

    def _set_detail_enabled(self, on: bool) -> None:
        for w in (self.b_tah, self.b_ode, self.b_borc, self.b_alacak, self.b_pdf, self.b_xls, self.b_edit,
                  self.b_del):
            w.setEnabled(on)
        self.b_del.setEnabled(on and self.svc.is_admin)

    def _show_detail(self) -> None:
        c = self.cur
        if not c:
            self.name.setText("Cari seçin")
            self.info.setText("Soldaki listeden bir cari seçin ya da 'Yeni Cari' ile ekleyin.")
            self.bal.setText("")
            self.bal_word.setText("")
            self.ekstre.set_rows([])
            self.ek_footer.setText("")
            self._set_detail_enabled(False)
            return
        try:
            c = self.svc.get_cari(c["id"])
        except ServiceError:
            self.cur = None
            self._show_detail()
            return
        self.cur = c
        self._set_detail_enabled(True)
        self.name.setText(c["unvan"] + ("  (pasif)" if not c["aktif"] else ""))
        bits = [c["kod"], c["tip_adi"]]
        for k in ("telefon", "eposta", "yetkili"):
            if c.get(k):
                bits.append(c[k])
        vd = " ".join(x for x in (c.get("vergi_dairesi"), c.get("vergi_no")) if x)
        if vd:
            bits.append(f"VD: {vd}")
        self.info.setText("  ·  ".join(b for b in bits if b))
        self.bal.setText(fmt_balance(c["bakiye"], True))
        self.bal.setStyleSheet(f"color: {_bal_color(c)};")
        self.bal_word.setText({"Borçlu": "Cari bize borçlu", "Alacaklı": "Biz cariye borçluyuz"}
                              .get(balance_word(c["bakiye"]), "Bakiye yok"))
        self._load_ekstre()

    def _load_ekstre(self) -> None:
        if not self.cur:
            return
        s, e = self.svc.span(*self.period.range())
        st = self.svc.cari_statement(self.cur["id"], s, e)
        rows = [{"id": None, "tarih": s.isoformat(), "islem": "Devir", "aciklama": "Önceki dönemden devreden",
                 "belge_no": "", "borc": 0, "alacak": 0, "bakiye": st["devir"], "_bold": True}] + st["satirlar"]
        self.ekstre.set_rows(rows, keep_selection_key=None)
        self.ek_footer.setText(
            f"<span style='color:{C['muted']}'>{len(st['satirlar'])} hareket</span> &nbsp;·&nbsp; "
            f"Dönem borç <b>{fmt_money(st['toplam_borc'])}</b> &nbsp;·&nbsp; "
            f"Dönem alacak <b>{fmt_money(st['toplam_alacak'])}</b> &nbsp;·&nbsp; "
            f"Dönem sonu bakiye <b style='color:{_bal_color(st)}'>{fmt_balance(st['bakiye'])}</b>")
        self._st = st

    def show_cari(self, cid) -> None:
        """Başka bir ekrandan (analiz, rapor) gelen cariyi filtreleri temizleyerek seçer."""
        try:
            c = self.svc.get_cari(cid)
        except ServiceError:
            return
        for w in (self.search, self.only_bal, self.inactive):
            w.blockSignals(True)
        self.search.clear()
        self.only_bal.setChecked(False)
        self.inactive.setChecked(not c["aktif"])
        self.tip.set_value("")
        for w in (self.search, self.only_bal, self.inactive):
            w.blockSignals(False)
        self.cur = {"id": cid}
        self._reload_list()
        self.list.select_where("id", cid)
        self._selected()

    # ------------------------------------------------------------ işlemler
    def _new(self) -> None:
        tip = self.tip.value() or "MUSTERI"
        dlg = CariDialog(self.ctx, self, default_tip=tip)
        if dlg.exec() and dlg.saved_id:
            self.cur = {"id": dlg.saved_id}
            self._reload_list()
            self.list.select_where("id", dlg.saved_id)

    def _edit_cari(self) -> None:
        if not self.cur:
            return
        CariDialog(self.ctx, self, cari=self.svc.get_cari(self.cur["id"])).exec()

    def _delete(self) -> None:
        if not self.cur:
            return
        c = self.svc.get_cari(self.cur["id"])
        if c["hareket_sayisi"]:
            if ask(self, "Cari Silinemez", f"'{c['unvan']}' carisinin {c['hareket_sayisi']} hareketi var; silinemez.\n\n"
                                           "Bunun yerine pasif yapılsın mı? (Listelerde ve seçimlerde görünmez, "
                                           "geçmiş kayıtlar korunur.)", yes="Pasif Yap"):
                self.svc.set_cari_active(c["id"], False)
                self.ctx.bus.changed.emit("cari")
            return
        if not ask(self, "Cari Sil", f"'{c['unvan']}' silinsin mi?", yes="Sil", danger=True):
            return
        try:
            self.svc.delete_cari(c["id"])
        except ServiceError as e:
            warn(self, "Silinemedi", str(e))
            return
        self.cur = None
        self.ctx.bus.changed.emit("cari")

    def _mov(self, tur: str) -> None:
        if not self.cur:
            return
        new_movement(self.ctx, self, tur, {"cari_id": self.cur["id"], "tarih": today()})

    def _open_movement(self, r) -> None:
        if r and r.get("id"):
            edit_movement(self.ctx, self, r)

    def _ekstre_pdf(self) -> None:
        if not self.cur:
            return
        c = self.svc.get_cari(self.cur["id"])
        s, e = self.svc.span(*self.period.range())
        if e > today():
            e = today()
        st = self.svc.cari_statement(c["id"], s, e)
        save_report(self.ctx, self, "Cari Ekstreler", f"Ekstre_{safe_name(c['unvan'])}_{e.isoformat()}.pdf",
                    lambda p: reports_pdf.cari_ekstre_pdf(p, c, st, s, e, self.svc.firma(), self.ctx.user_label()))

    def _ekstre_excel(self) -> None:
        if not self.cur:
            return
        c = self.svc.get_cari(self.cur["id"])
        s, e = self.svc.span(*self.period.range())
        st = self.svc.cari_statement(c["id"], s, e)
        rows = [[s.isoformat(), "Devir", "", "Önceki dönemden devreden", None, None, st["devir"]]]
        rows += [[m["tarih"], m["islem"], m["belge_no"], m["aciklama"], m["borc"] or None, m["alacak"] or None,
                  m["bakiye"]] for m in st["satirlar"]]
        export_excel(self.ctx, self, f"Ekstre_{c['unvan']}.xlsx", [{
            "name": "Ekstre", "title": f"{c['unvan']} · Cari Ekstre · {fmt_date(s)} – {fmt_date(e)}",
            "headers": ["Tarih", "İşlem", "Belge No", "Açıklama", "Borç", "Alacak", "Bakiye (+B / −A)"],
            "types": ["date", "text", "text", "text", "money", "money", "money"], "rows": rows,
            "totals": ["", "Dönem Toplamı", "", "", st["toplam_borc"], st["toplam_alacak"], st["bakiye"]]}])

    def _list_rows(self):
        return self.list.visible_rows()

    def _list_pdf(self) -> None:
        rows = self._list_rows()
        sub = {"MUSTERI": "Müşteriler", "TEDARIKCI": "Tedarikçiler"}.get(self.tip.value() or "", "Tüm cariler")
        if self.only_bal.isChecked():
            sub += " · bakiyesi olanlar"
        save_report(self.ctx, self, "Cari Listeler", f"Cari_Bakiye_Listesi_{today().isoformat()}.pdf",
                    lambda p: reports_pdf.cari_listesi_pdf(p, rows, self.svc.firma(), self.ctx.user_label(), sub))

    def _export_list(self) -> None:
        rows = self._list_rows()
        export_excel(self.ctx, self, f"Cariler_{today().isoformat()}.xlsx", [{
            "name": "Cariler", "title": f"Cari Bakiye Listesi · {fmt_date(today())}",
            "headers": ["Kod", "Ünvan", "Tip", "Yetkili", "Telefon", "E-posta", "Vergi Dairesi", "Vergi No", "Adres",
                        "Borç", "Alacak", "Bakiye (+B / −A)", "Durum", "Son Hareket"],
            "types": ["text"] * 9 + ["money", "money", "money", "text", "date"],
            "rows": [[c["kod"], c["unvan"], c["tip_adi"], c["yetkili"], c["telefon"], c["eposta"], c["vergi_dairesi"],
                      c["vergi_no"], c["adres"], c["borc"], c["alacak"], c["bakiye"], c["durum"], c["son_hareket"]]
                     for c in rows],
            "totals": ["", f"{len(rows)} cari", "", "", "", "", "", "", "", sum(c["borc"] for c in rows),
                       sum(c["alacak"] for c in rows), sum(c["bakiye"] for c in rows), "", None]}])
