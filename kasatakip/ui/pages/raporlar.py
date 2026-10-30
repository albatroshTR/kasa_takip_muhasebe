"""Raporlar: hazır rapor listesi, parametreler, ekranda önizleme, PDF ve Excel çıktısı."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea,
                               QVBoxLayout, QWidget)

from ... import reports
from ...analytics import Analytics
from ...errors import ServiceError
from ...util import fmt_dt, today
from ..context import export_report_excel, open_path, open_target, report_pdf
from ..icons import svg_icon
from ..report_view import ReportView
from ..theme import C
from ..widgets import (Card, FlowLayout, PeriodSelector, SearchCombo, button, combo, combo_select, date_edit,
                       muted, pydate)
from .base import Page, log


class RaporlarPage(Page):
    title = "Raporlar"
    subtitle = "Hazır raporlar: parametreleri seçin, ekranda önizleyin, PDF olarak yazdırın veya Excel'e aktarın"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.rd: reports.ReportDef | None = None
        self.rep = None
        self.getters: dict = {}
        self.refreshers: list = []
        self._building = False
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # ------------------------------------------------ sol: rapor listesi
        lv = QVBoxLayout()
        lv.setSpacing(12)
        left = Card(margins=(10, 12, 10, 10), spacing=6)
        left.setFixedWidth(312)
        self.list = QListWidget()
        self.list.setObjectName("ReportList")
        self.list.setIconSize(QSize(18, 18))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setStyleSheet(
            "QListWidget#ReportList { border: none; background: transparent; outline: none; }"
            "QListWidget#ReportList::item { padding: 6px 6px; border-radius: 6px; color: %s; }"
            "QListWidget#ReportList::item:selected { background: %s; color: %s; font-weight: 700; }"
            "QListWidget#ReportList::item:hover:!selected { background: #F3F6FB; }"
            % (C["text"], C["primary_soft"], C["primary_dark"]))
        left.add(self.list, 1)
        lv.addWidget(left, 1)
        rc = Card("Son Oluşturulanlar", margins=(12, 10, 12, 10), spacing=6)
        rc.setFixedWidth(312)
        rc.setMaximumHeight(200)
        self.recent = QListWidget()
        self.recent.setStyleSheet("QListWidget { border: none; background: transparent; }"
                                  "QListWidget::item { padding: 4px 2px; }")
        self.recent.itemActivated.connect(lambda it: open_path(it.data(Qt.UserRole)))
        self.recent.setToolTip("Çift tıklayınca açılır")
        rc.add(self.recent, 1)
        rc.add(button("Rapor Klasörünü Aç", "folder", callback=lambda: open_path(self._report_dir())))
        lv.addWidget(rc)
        root.addLayout(lv)

        # ------------------------------------------------ sağ: parametreler + önizleme
        rv = QVBoxLayout()
        rv.setSpacing(10)
        head = Card(margins=(16, 12, 16, 12), spacing=8)
        tl = QHBoxLayout()
        self.r_icon = QLabel()
        self.r_icon.setFixedSize(36, 36)
        self.r_icon.setAlignment(Qt.AlignCenter)
        self.r_icon.setStyleSheet(f"background: {C['primary_soft']}; border-radius: 9px;")
        tl.addWidget(self.r_icon)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        self.r_title = QLabel("Rapor seçin")
        self.r_title.setObjectName("SectionTitle")
        self.r_title.setStyleSheet("font-size: 14pt;")
        self.r_desc = muted("")
        tv.addWidget(self.r_title)
        tv.addWidget(self.r_desc)
        tl.addLayout(tv, 1)
        head.add(tl)
        self.param_box = QWidget()
        self.flow = FlowLayout(self.param_box)
        head.add(self.param_box)
        br = QHBoxLayout()
        br.setSpacing(8)
        self.r_info = muted("", small=True)
        br.addWidget(self.r_info, 1)
        self.b_prev = button("Önizlemeyi Yenile", "refresh", tip="F5", callback=self._preview)
        self.b_pdf = button("PDF Oluştur", "pdf", "primary", "Raporu PDF olarak kaydeder ve açar (yazdırmak için)",
                            self._pdf)
        self.b_xls = button("Excel'e Aktar", "excel", callback=self._excel)
        for b in (self.b_prev, self.b_xls, self.b_pdf):
            b.setMinimumHeight(34)
            br.addWidget(b)
        head.add(br)
        rv.addWidget(head)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("PageBody")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 8, 8)
        self.view = ReportView()
        self.view.activated.connect(lambda kind, key: open_target(self.ctx, self, kind, key))
        il.addWidget(self.view)
        sa.setWidget(inner)
        rv.addWidget(sa, 1)
        root.addLayout(rv, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._preview)
        self._fill_list()
        self.list.currentItemChanged.connect(lambda cur, _: self._select(cur))

    # ------------------------------------------------------------ liste
    def _fill_list(self) -> None:
        self.list.clear()
        group = None
        for rd in reports.available(self.svc):
            if rd.group != group:
                group = rd.group
                it = QListWidgetItem(group.upper())
                it.setFlags(Qt.NoItemFlags)
                f = QFont(self.font())
                f.setBold(True)
                f.setPointSizeF(max(7.5, f.pointSizeF() - 1.5))
                it.setFont(f)
                it.setForeground(QColor(C["muted"]))
                it.setSizeHint(QSize(0, 30))
                self.list.addItem(it)
            it = QListWidgetItem(svg_icon(rd.icon, C["primary"], 18), rd.title)
            it.setData(Qt.UserRole, rd.key)
            it.setToolTip(rd.description)
            self.list.addItem(it)

    def select_report(self, key: str) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == key:
                self.list.setCurrentItem(it)
                return

    def _select(self, item) -> None:
        if item is None or not item.data(Qt.UserRole):
            return
        rd = reports.REPORT_BY_KEY[item.data(Qt.UserRole)]
        self.rd = rd
        self.r_title.setText(rd.title)
        self.r_desc.setText(rd.description)
        self.r_icon.setPixmap(svg_icon(rd.icon, C["primary"], 20).pixmap(20, 20))
        self._build_params(rd)
        self._preview()

    # ------------------------------------------------------------ parametreler
    def _build_params(self, rd) -> None:
        self._building = True
        while self.flow.count():
            it = self.flow.takeAt(0)
            w = it.widget()
            if w:
                w.hide()               # deleteLater beklerken eski alanlar görünmesin
                w.deleteLater()
        self.getters, self.refreshers = {}, []
        for p in rd.params:
            box, getter = self._param_widget(p)
            self.flow.addWidget(box)
            self.getters[p.key] = getter
        self._building = False
        self.param_box.updateGeometry()

    def _changed(self, *_) -> None:
        if not self._building:
            self._timer.start()

    def _param_widget(self, p):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        if p.kind != "check":
            lab = QLabel(p.label)
            lab.setProperty("small", True)
            v.addWidget(lab)
        svc = self.svc
        if p.kind == "date":
            w = date_edit(today())
            w.dateChanged.connect(self._changed)
            getter = lambda w=w: pydate(w.date())   # noqa: E731
        elif p.kind == "period":
            w = PeriodSelector(p.default or "Bu Ay")
            w.changed.connect(self._changed)
            getter = w.range
        elif p.kind in ("cari", "cari_opt"):
            w = SearchCombo(placeholder="Cari seçin (yazarak arayın)…" if p.kind == "cari" else "Tüm cariler")
            w.setMinimumWidth(300)

            def fill(w=w, opt=p.kind == "cari_opt"):
                w.set_items([(c["unvan"] + ("" if c["aktif"] else " (pasif)"), c["id"])
                             for c in svc.caris(include_inactive=True)], none_text="" if opt else None)
            fill()
            if p.kind == "cari":
                w.setCurrentIndex(-1)
                w.setEditText("")
            self.refreshers.append(fill)
            w.currentIndexChanged.connect(self._changed)
            w.lineEdit().editingFinished.connect(self._changed)

            def getter(w=w):
                cid = w.current_id()
                return None if cid is SearchCombo.INVALID else cid
        elif p.kind == "personel":
            w = SearchCombo(placeholder="Tüm personel (seçmek için yazın)")
            w.setMinimumWidth(300)

            def fill(w=w):
                w.set_items([(x["ad_soyad"] + ("" if x["aktif"] else " (pasif)"), x["id"])
                             for x in svc.personel_listesi(include_inactive=True)], none_text="")
            fill()
            self.refreshers.append(fill)
            w.currentIndexChanged.connect(self._changed)
            w.lineEdit().editingFinished.connect(self._changed)

            def getter(w=w):
                pid = w.current_id()
                return None if pid is SearchCombo.INVALID else pid
        elif p.kind == "odeme":
            w = combo([("Tüm ödeme tipleri", None)] + [(x["ad"], x["id"]) for x in svc.payment_types()])
            w.currentIndexChanged.connect(self._changed)
            getter = w.currentData
        elif p.kind == "kategori":
            w = combo([("Tüm kategoriler", None)] + [(f"{k['ad']} ({'Gelir' if k['tur'] == 'GELIR' else 'Gider'})",
                                                      k["id"]) for k in svc.categories()])
            w.setMinimumWidth(200)
            w.currentIndexChanged.connect(self._changed)
            getter = w.currentData
        elif p.kind == "choice":
            w = combo(p.choices or [])
            combo_select(w, p.default)
            w.currentIndexChanged.connect(self._changed)
            getter = w.currentData
        elif p.kind == "year":
            years = Analytics(svc).years()
            w = combo([(str(y), y) for y in years])
            combo_select(w, today().year)          # ileri tarihli kayıt olsa bile varsayılan: bu yıl
            w.setMinimumWidth(90)
            w.currentIndexChanged.connect(self._changed)
            getter = w.currentData
        elif p.kind == "check":
            w = QCheckBox(p.label)
            w.setChecked(bool(p.default))
            w.toggled.connect(self._changed)
            v.addSpacing(18)
            getter = w.isChecked
        elif p.kind == "user":
            users, _ = svc.audit_filters()
            w = combo([("Tüm kullanıcılar", None)] + [(u, u) for u in users])
            w.currentIndexChanged.connect(self._changed)
            getter = w.currentData
        else:
            w = QLineEdit()
            w.setPlaceholderText("Boş bırakılabilir")
            w.setClearButtonEnabled(True)
            w.setMinimumWidth(200)
            w.textChanged.connect(self._changed)
            getter = lambda w=w: w.text().strip()   # noqa: E731
        v.addWidget(w)
        box.setProperty("param_key", p.key)
        return box, getter

    def values(self) -> dict:
        return {k: g() for k, g in self.getters.items()}

    # ------------------------------------------------------------ önizleme / çıktı
    def refresh(self) -> None:
        if self.rd is None:
            if self.list.count():
                self.select_report("gunluk_kasa")
        else:
            for f in self.refreshers:
                f()
            self._preview()
        self._fill_recent()

    def _preview(self) -> None:
        self._timer.stop()
        if self.rd is None:
            return
        try:
            rep = reports.build(self.rd.key, self.svc, self.values())
        except ServiceError as e:
            self.rep = None
            self.view.show_message(str(e))
            self.r_info.setText("")
            self._set_enabled(False)
            return
        except Exception as e:  # noqa: BLE001
            log.exception("Rapor önizlemesi")
            self.rep = None
            self.view.show_message(f"Rapor hazırlanamadı: {e}")
            self._set_enabled(False)
            return
        self.rep = rep
        self.view.show_report(rep)
        self.r_info.setText(" · ".join(x for x in rep.lines if x))
        self._set_enabled(True)

    def _set_enabled(self, on: bool) -> None:
        self.b_pdf.setEnabled(on)
        self.b_xls.setEnabled(on)

    def _pdf(self) -> None:
        if self.rd is None:
            return
        if self.rep is None:
            self._preview()
        if self.rep is not None:
            if report_pdf(self.ctx, self, self.rd.key, self.values(), self.rep):
                self._fill_recent()

    def _excel(self) -> None:
        if self.rep is None:
            self._preview()
        if self.rep is not None:
            if export_report_excel(self.ctx, self, self.rep):
                self._fill_recent()

    # ------------------------------------------------------------ son raporlar
    def _report_dir(self) -> Path:
        d = self.ctx.report_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _fill_recent(self) -> None:
        self.recent.clear()
        base = self._report_dir()
        try:
            files = [p for p in base.rglob("*") if p.suffix.lower() in (".pdf", ".xlsx") and p.is_file()]
            if not self.svc.is_admin:      # yönetim raporları (işlem geçmişi) yalnız yöneticiye listelenir
                files = [p for p in files if "Yönetim Raporları" not in p.parts
                         and not p.name.lower().startswith("islem_gecmisi")]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            files = []
        for p in files[:10]:
            it = QListWidgetItem(svg_icon("pdf" if p.suffix.lower() == ".pdf" else "excel", C["muted"], 16), p.name)
            it.setData(Qt.UserRole, str(p))
            it.setToolTip(f"{p}\n{fmt_dt(dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(' '))}")
            self.recent.addItem(it)
        if not files:
            it = QListWidgetItem("Henüz rapor oluşturulmadı")
            it.setFlags(Qt.NoItemFlags)
            self.recent.addItem(it)
