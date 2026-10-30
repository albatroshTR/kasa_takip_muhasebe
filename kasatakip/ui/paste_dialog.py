"""Excel'den kopyalanan satırları önizleyip toplu kaydetme penceresi."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..errors import ServiceError
from ..paste import (CARI_ALANLARI, HAREKET_ALANLARI, Baglam, hazirla_cariler, hazirla_hareketler, infer_cari_mapping,
                     infer_hareket_mapping, parse_table_text)
from ..util import today
from .theme import C
from .widgets import button, combo, combo_select, date_edit, info, muted, pydate

MAX_ROWS = 2000


class PasteImportDialog(QDialog):
    def exec(self) -> int:
        try:
            return super().exec()
        finally:
            self.deleteLater()      # bkz. BaseDialog.exec

    def __init__(self, ctx, parent=None, mode: str = "hareket", default_date=None, default_tur: str = "GELIR",
                 text: str | None = None):
        super().__init__(parent)
        self.ctx, self.svc, self.mode = ctx, ctx.svc, mode
        self.rows: list[list[str]] = []
        self.results = []
        self.saved_count = 0
        self.setWindowTitle("Excel'den Yapıştır")
        self.resize(1080, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(10)
        t = QLabel("Excel'den Yapıştır — " + ("Hareketler" if mode == "hareket" else "Cariler"))
        t.setObjectName("SectionTitle")
        v.addWidget(t)
        if mode == "hareket":
            v.addWidget(muted("Excel'de satırları seçip Ctrl+C ile kopyalayın, sonra 'Panodan Yapıştır'a basın (veya "
                              "Ctrl+V). Sütunlar otomatik tanınır; tanınmayanları ilk satırdaki listeden seçebilirsiniz. "
                              "Tarih, tür veya ödeme tipi sütunu yoksa aşağıdaki varsayılanlar kullanılır."))
        else:
            v.addWidget(muted("Excel'deki cari listesini kopyalayıp yapıştırın. 'Bakiye' sütunu varsa pozitif tutar "
                              "borçlu, negatif tutar alacaklı açılış bakiyesi olarak işlenir."))

        opt = QHBoxLayout()
        opt.setSpacing(8)
        self.header_chk = QCheckBox("İlk satır başlık")
        if mode == "hareket":
            self.d_date = date_edit(default_date or today())
            self.d_tur = combo([("Gelir", "GELIR"), ("Gider", "GIDER")])
            combo_select(self.d_tur, default_tur)
            pts = [p for p in self.svc.payment_types() if p["aktif"]]
            self.d_odeme = combo([(p["ad"], p["id"]) for p in pts])
            self.new_cat = QCheckBox("Eksik kategorileri oluştur")
            self.new_cat.setEnabled(self.svc.is_admin)
            if not self.svc.is_admin:
                self.new_cat.setToolTip("Kategori eklemek yönetici yetkisi gerektirir")
            self.new_cari = QCheckBox("Eksik carileri oluştur")
            for lab, w in (("Varsayılan tarih", self.d_date), ("tür", self.d_tur), ("ödeme", self.d_odeme)):
                lb = QLabel(lab)
                lb.setProperty("muted", True)
                opt.addWidget(lb)
                opt.addWidget(w)
            opt.addSpacing(10)
            for w in (self.header_chk, self.new_cat, self.new_cari):
                opt.addWidget(w)
            for w in (self.d_date,):
                w.dateChanged.connect(self._revalidate)
            for w in (self.d_tur, self.d_odeme):
                w.currentIndexChanged.connect(self._revalidate)
            for w in (self.new_cat, self.new_cari):
                w.toggled.connect(self._revalidate)
        else:
            self.d_tip = combo([("Müşteri", "MUSTERI"), ("Tedarikçi", "TEDARIKCI"), ("Her İkisi", "HER_IKISI")])
            self.d_acilis = date_edit(today())
            for lab, w in (("Varsayılan cari tipi", self.d_tip), ("Açılış bakiyesi tarihi", self.d_acilis)):
                lb = QLabel(lab)
                lb.setProperty("muted", True)
                opt.addWidget(lb)
                opt.addWidget(w)
            opt.addSpacing(10)
            opt.addWidget(self.header_chk)
            self.d_tip.currentIndexChanged.connect(self._revalidate)
            self.d_acilis.dateChanged.connect(self._revalidate)
        self.header_chk.toggled.connect(self._revalidate)
        opt.addStretch(1)
        v.addLayout(opt)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        v.addWidget(self.table, 1)
        self.empty = muted("Henüz veri yapıştırılmadı. Excel'de satırları kopyalayıp 'Panodan Yapıştır'a basın.")
        self.empty.setAlignment(Qt.AlignCenter)
        v.addWidget(self.empty)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        bl = QHBoxLayout()
        bl.addWidget(button("Panodan Yapıştır", "paste", "primary" if text is None else None,
                            tip="Ctrl+V", callback=self.paste_clipboard))
        bl.addWidget(button("Temizle", "trash", callback=lambda: self.load_text("")))
        bl.addStretch(1)
        bl.addWidget(button("İptal", callback=self.reject))
        self.save_btn = button("Kaydet", "check", "primary", callback=self._save)
        self.save_btn.setEnabled(False)
        bl.addWidget(self.save_btn)
        v.addLayout(bl)
        QShortcut(QKeySequence.Paste, self, activated=self.paste_clipboard)
        self.mapping_combos = []
        if text:
            self.load_text(text)
        else:
            self._render()

    # ------------------------------------------------------------ veri
    def paste_clipboard(self) -> None:
        text = QGuiApplication.clipboard().text()
        if not text.strip():
            info(self, "Pano boş", "Panoda yapıştırılacak veri yok. Önce Excel'de satırları seçip Ctrl+C yapın.")
            return
        self.load_text(text)

    def load_text(self, text: str) -> None:
        self.rows = parse_table_text(text)[:MAX_ROWS + 1]
        if self.mode == "hareket":
            mapping, header = infer_hareket_mapping(self.rows, Baglam.from_service(self.svc))
        else:
            mapping, header = infer_cari_mapping(self.rows)
        self.header_chk.blockSignals(True)
        self.header_chk.setChecked(header)
        self.header_chk.blockSignals(False)
        self._render(mapping)

    def _render(self, mapping=None) -> None:
        self.table.clear()
        self.mapping_combos = []
        has = bool(self.rows)
        self.table.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.summary.setText("")
            self.save_btn.setEnabled(False)
            self.save_btn.setText("Kaydet")
            return
        ncol = len(self.rows[0])
        self.table.setColumnCount(ncol + 2)
        self.table.setRowCount(len(self.rows) + 1)
        self.table.setHorizontalHeaderLabels(["Durum", "Sonuç / Hata"] + [f"Sütun {i + 1}" for i in range(ncol)])
        fields = HAREKET_ALANLARI if self.mode == "hareket" else CARI_ALANLARI
        lab = QTableWidgetItem("Sütun türü →")
        lab.setForeground(QColor(C["muted"]))
        self.table.setItem(0, 0, lab)
        self.table.setSpan(0, 0, 1, 2)
        for c in range(ncol):
            cb = combo([(t, k) for k, t in fields])
            combo_select(cb, mapping[c] if mapping and c < len(mapping) else "yok")
            cb.currentIndexChanged.connect(self._revalidate)
            self.table.setCellWidget(0, c + 2, cb)
            self.mapping_combos.append(cb)
        for r, row in enumerate(self.rows, start=1):
            for c, val in enumerate(row):
                it = QTableWidgetItem(val)
                self.table.setItem(r, c + 2, it)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 420)
        for c in range(ncol):
            self.table.setColumnWidth(c + 2, 150)
        self.table.setRowHeight(0, 38)
        self._revalidate()

    def _mapping(self) -> list[str]:
        return [cb.currentData() for cb in self.mapping_combos]

    def _revalidate(self, *_):
        if not self.rows:
            return
        ctx = Baglam.from_service(self.svc)
        mapping = self._mapping()
        header = self.header_chk.isChecked()
        if self.mode == "hareket":
            self.results = hazirla_hareketler(self.rows, mapping, ctx, {
                "baslik_var": header, "varsayilan_tarih": pydate(self.d_date.date()),
                "varsayilan_tur": self.d_tur.currentData(), "varsayilan_odeme_id": self.d_odeme.currentData(),
                "kategori_olustur": self.new_cat.isChecked(), "cari_olustur": self.new_cari.isChecked()})
        else:
            self.results = hazirla_cariler(self.rows, mapping, ctx, {
                "baslik_var": header, "varsayilan_tip": self.d_tip.currentData(),
                "acilis_tarihi": pydate(self.d_acilis.date())})
        by_no = {res.no: res for res in self.results}
        ok_n = sum(1 for res in self.results if res.gecerli)
        bad_n = len(self.results) - ok_n
        bold = QFont()
        bold.setBold(True)
        for r in range(1, len(self.rows) + 1):
            res = by_no.get(r)
            if res is None:            # başlık satırı
                st, txt, color, bg = "Başlık", "Bu satır başlık; kaydedilmez.", C["muted"], QColor("#F1F4F9")
            elif res.gecerli:
                st = "✓ Geçerli" + (" (uyarı)" if res.uyari else "")
                txt = res.ozet + (f"  —  {res.uyari}" if res.uyari else "")
                color, bg = C["gelir"], QColor("#F4FBF6")
            else:
                st, txt, color, bg = "✗ Hatalı", res.hata, C["gider"], QColor("#FFF5F5")
            i0 = QTableWidgetItem(st)
            i0.setForeground(QColor(color))
            i0.setFont(bold)
            i0.setToolTip(res.hata if res and not res.gecerli else (res.uyari if res else ""))
            i1 = QTableWidgetItem(txt)
            i1.setToolTip(txt)
            if res is not None and not res.gecerli:
                i1.setForeground(QColor(C["gider"]))
            self.table.setItem(r, 0, i0)
            self.table.setItem(r, 1, i1)
            for c in range(self.table.columnCount()):
                it = self.table.item(r, c)
                if it is not None:
                    it.setBackground(bg)
        parts = [f"<b style='color:{C['gelir']}'>{ok_n} satır kaydedilmeye hazır</b>"]
        if bad_n:
            parts.append(f"<b style='color:{C['gider']}'>{bad_n} satır hatalı</b> (kaydedilmeyecek; sebebi 'Durum' "
                         "sütununda)")
        new_cats = {res.yeni_kategori for res in self.results if res.gecerli and res.yeni_kategori}
        new_caris = {res.yeni_cari for res in self.results if res.gecerli and res.yeni_cari}
        if new_cats:
            parts.append(f"{len(new_cats)} yeni kategori açılacak")
        if new_caris:
            parts.append(f"{len(new_caris)} yeni cari açılacak")
        self.summary.setText(" · ".join(parts))
        self.save_btn.setEnabled(ok_n > 0)
        self.save_btn.setText(f"{ok_n} Kaydı Ekle" if ok_n else "Kaydet")

    # ------------------------------------------------------------ kaydet
    def _save(self) -> None:
        good = [r for r in self.results if r.gecerli]
        if not good:
            return
        try:
            if self.mode == "hareket":
                n = self.svc.import_movements([{"veri": r.veri, "yeni_kategori": r.yeni_kategori,
                                                "yeni_cari": r.yeni_cari} for r in good])
                self.ctx.bus.changed.emit("hareket")
            else:
                n = self.svc.import_caris([{"veri": r.veri, "acilis": r.acilis} for r in good])
                self.ctx.bus.changed.emit("cari")
        except ServiceError as e:
            info(self, "Kaydedilemedi", f"Hiçbir satır kaydedilmedi:\n\n{e}")
            return
        self.saved_count = n
        self.accept()
