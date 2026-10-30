"""Yedekleme: otomatik/manuel yedekler, ek yedek klasörü, geri yükleme, tüm verinin Excel'e aktarımı."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout

from ... import sifreleme
from ...backup import validate_backup
from ...errors import ServiceError
from ...util import fmt_date, fmt_dt, today
from ..context import export_excel, open_path
from ..login import KilitAcmaDialog
from ..widgets import Card, Col, DataTable, ask, button, error, info, muted
from .base import Page


class YedeklemePage(Page):
    title = "Yedekleme"
    subtitle = "Verileriniz otomatik yedeklenir; istediğiniz an elle yedek alabilir veya geri yükleyebilirsiniz"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(14)
        ic = Card("Yedekleme Durumu")
        g = QGridLayout()
        g.setVerticalSpacing(8)
        g.setHorizontalSpacing(12)
        self.l_last = QLabel()
        self.l_last.setStyleSheet("font-weight: 700;")
        self.l_dir = QLabel()
        self.l_dir.setWordWrap(True)
        self.l_extra = QLabel()
        self.l_extra.setWordWrap(True)
        rows = (("Son yedek", self.l_last), ("Yedek klasörü", self.l_dir), ("Ek kopya klasörü", self.l_extra))
        for i, (t, w) in enumerate(rows):
            lb = QLabel(t)
            lb.setProperty("muted", True)
            g.addWidget(lb, i, 0)
            g.addWidget(w, i, 1)
        g.setColumnStretch(1, 1)
        ic.add(g)
        b1 = QHBoxLayout()
        b1.addWidget(button("Yedek Klasörünü Aç", "folder",
                            callback=lambda: open_path(self.ctx.backups.backup_dir)))
        self.b_extra = button("Ek Klasör Seç…", "folder", tip="USB bellek, ağ klasörü veya OneDrive/Google Drive "
                                                             "klasörü", callback=self._choose_extra)
        self.b_extra_clear = button("Ek Klasörü Kaldır", callback=self._clear_extra)
        b1.addWidget(self.b_extra)
        b1.addWidget(self.b_extra_clear)
        b1.addStretch(1)
        ic.add(b1)
        ic.add(muted("Otomatik yedek alınan anlar: oturum açılışı (6 saatte bir), program kapanışı ve her gün "
                     "kapanışı. Son 7 günün tüm yedekleri, daha eskilerin her günden son yedeği saklanır; her ayın "
                     "son hali 'Aylık' klasöründe süresiz tutulur. Elle alınan yedekler hiç silinmez. Yedekler "
                     "şifreli saklanır; başka bir bilgisayarda yedeğin alındığı anda geçerli olan bir program "
                     "şifresiyle ya da kurtarma anahtarıyla açılır.", small=True))
        kr = QHBoxLayout()
        kr.addWidget(muted("Günlük yedek saklama süresi (gün):", wrap=False))
        self.keep = QSpinBox()
        self.keep.setRange(14, 3650)
        self.keep.setFixedWidth(90)
        kr.addWidget(self.keep)
        kr.addStretch(1)
        ic.add(kr)
        self.keep.editingFinished.connect(self._save_keep)
        top.addWidget(ic, 3)

        ac = Card("İşlemler")
        self.b_now = button("Şimdi Yedekle", "database", "primary", callback=self._backup_now)
        self.b_restore = button("Seçili Yedeği Geri Yükle", "refresh", callback=self._restore_selected)
        self.b_restore_file = button("Dosyadan Geri Yükle…", "folder", callback=self._restore_file)
        self.b_excel = button("Tüm Verileri Excel'e Aktar", "excel", callback=self._export_all)
        for b in (self.b_now, self.b_restore, self.b_restore_file, self.b_excel):
            b.setMinimumHeight(36)
            ac.add(b)
        ac.add(muted("Geri yüklemeden önce mevcut durumun yedeği otomatik alınır; yanlış yedek seçilse bile "
                     "geri dönülebilir.", small=True))
        ac.lay.addStretch(1)
        top.addWidget(ac, 2)
        root.addLayout(top)

        lc = Card("Yedekler")
        self.table = DataTable([Col("zaman_gosterim", "Tarih / Saat", width=150),
                                Col("tur_adi", "Tür", width=220), Col("boyut_gosterim", "Boyut", width=90, align="r"),
                                Col("dosya", "Dosya", stretch=True)], multi=False)
        self.table.rowActivated.connect(lambda r: self._restore_selected())
        lc.add(self.table, 1)
        root.addWidget(lc, 1)

    def refresh(self) -> None:
        bm = self.ctx.backups
        last = bm.last_backup_time()
        self.l_last.setText(fmt_dt(last) if last else "Henüz yedek alınmadı")
        self.l_dir.setText(str(bm.backup_dir))
        ex = bm.extra_dir()
        self.l_extra.setText(str(ex) if ex else "Tanımlı değil (önerilir: USB bellek veya bulut klasörü)")
        self.keep.setValue(bm.keep_days())
        admin = self.svc.is_admin
        for w in (self.b_extra, self.b_extra_clear, self.keep, self.b_restore, self.b_restore_file):
            w.setEnabled(admin)
        self.b_extra_clear.setEnabled(admin and ex is not None)
        if not admin:
            self.b_restore.setToolTip("Geri yükleme yalnızca yöneticiye açıktır")
        self.table.set_rows(bm.list_backups(), keep_selection_key="path")

    # ------------------------------------------------------------ işlemler
    def _backup_now(self) -> None:
        try:
            res = self.ctx.backups.backup("manuel")
        except Exception as e:  # noqa: BLE001
            error(self, "Yedek alınamadı", str(e))
            return
        msg = f"Yedek alındı:\n{res.path}"
        if res.extra_ok is True:
            msg += "\n\nEk klasöre de kopyalandı."
        elif res.extra_ok is False:
            msg += f"\n\nUYARI: Ek klasöre kopyalanamadı ({res.extra_error}). Klasör takılı/erişilebilir mi?"
        info(self, "Yedekleme", msg)
        self.ctx.bus.changed.emit("yedek")

    def _choose_extra(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Ek yedek klasörünü seçin")
        if not d:
            return
        self.svc.set_setting("ek_yedek_klasoru", d)
        self.svc.log("AYAR", "Yedek", None, f"Ek yedek klasörü: {d}")
        if ask(self, "Ek Yedek Klasörü", f"Ek klasör ayarlandı:\n{d}\n\nŞimdi bir yedek alınıp oraya da kopyalansın mı?",
               yes="Yedekle"):
            self._backup_now()
        self.refresh()

    def _clear_extra(self) -> None:
        self.svc.set_setting("ek_yedek_klasoru", "")
        self.svc.log("AYAR", "Yedek", None, "Ek yedek klasörü kaldırıldı")
        self.refresh()

    def _save_keep(self) -> None:
        if self.svc.is_admin and str(self.keep.value()) != self.svc.setting("yedek_saklama_gun", "90"):
            self.svc.set_setting("yedek_saklama_gun", self.keep.value())
            self.svc.log("AYAR", "Yedek", None, f"Yedek saklama süresi {self.keep.value()} gün")

    def _restore(self, path: str) -> None:
        giris = {}
        ok, msg, inf = validate_backup(path, self.ctx.db.anahtar)
        if not ok and inf.get("anahtar_gerekli"):
            # başka kurulumun (bilgisayarın) şifreli yedeği: o yedekteki bir şifre ya da kurtarma anahtarı gerekir
            found = {}

            def dene(g: dict) -> None:
                r = validate_backup(path, None, **g)
                if not r[0]:
                    raise ServiceError(r[1])
                found["giris"], found["res"] = g, r

            text = (f"{Path(path).name}\n\nBu yedek başka bir kurulumda (bilgisayarda) alınmış. Açmak için yedeğin "
                    "alındığı tarihte geçerli olan bir kullanıcı adı ve şifresini ya da o tarihteki kurtarma "
                    "anahtarını yazın. Geri yüklemeden sonra veriler bu yedeğin şifreleriyle açılır.")
            if KilitAcmaDialog(self, text, dene, "Devam").exec() != QDialog.Accepted or "giris" not in found:
                return
            giris, (ok, msg, inf) = found["giris"], found["res"]
        if not ok:
            error(self, "Geri yüklenemez", msg)
            return
        text = (f"Bu yedek geri yüklensin mi?\n\n{Path(path).name}\n"
                f"İçerik: {inf['hareket']} hareket, {inf['cari']} cari, son kayıt tarihi "
                f"{fmt_date(inf['son_tarih']) or '-'}\n\nMevcut veriler (kullanıcılar ve şifreler dahil) bu yedekteki "
                "hale döner. Bu bilgisayara ait ayarlar (ek yedek klasörü, saklama süresi, rapor klasörü, kurtarma "
                "anahtarı) korunur. İşlemden önce mevcut durumun yedeği otomatik alınır. Sonra yeniden giriş "
                "yapmanız istenecek.")
        if giris:
            text += ("\n\nYedek başka bir kurulumdan geldiği için veriler bundan sonra bu yedeğin şifreleriyle ve "
                     "kurtarma anahtarıyla açılır (bu bilgisayarın kurtarma anahtarı geçersiz olur). Yedekte kurtarma "
                     "anahtarı yoksa yönetici girişinde yeni bir anahtar gösterilir.")
        if not ask(self, "Geri Yükle", text, yes="Geri Yükle", danger=True):
            return
        try:
            pre = self.ctx.backups.restore(path, **giris)
        except sifreleme.AnahtarGerekli as e:
            # yedekteki yönetici şifresi bugünkünden farklı: yedekteki bir yöneticinin şifresiyle ya da (şifre
            # hatırlanmıyorsa) kurtarma anahtarıyla geri yüklenir; anahtar yazılırsa yedeği açan bilgi yerine geçer
            found = {}

            def geri_yukle(g: dict) -> None:
                if "kurtarma_anahtari" in g:
                    found["pre"] = self.ctx.backups.restore(path, **g)
                else:
                    found["pre"] = self.ctx.backups.restore(path, yonetici=(g["kullanici"], g["sifre"]), **giris)

            if KilitAcmaDialog(self, f"{Path(path).name}\n\n{e}", geri_yukle,
                               "Geri Yükle").exec() != QDialog.Accepted or "pre" not in found:
                return
            pre = found["pre"]
        except ServiceError as e:
            error(self, "Geri yüklenemedi", str(e))
            return
        text = f"Veriler geri yüklendi.\nÖnceki durumun yedeği: {pre.path.name}"
        if pre.kurtarma_ile:
            text += ("\n\nVerilerde yönetici şifresinin kilidi yok. Giriş ekranında \"Şifremi unuttum\"a basıp az önce "
                     "yazdığınız kurtarma anahtarıyla yeni yönetici şifresi belirleyin.")
        elif pre.sifreler_degisti:
            text += ("\n\nŞifreler artık yedeğin alındığı tarihte geçerli olan şifrelerdir. Hatırlamıyorsanız giriş "
                     "ekranındaki \"Şifremi unuttum\" ile kurtarma anahtarını kullanın.")
        info(self, "Geri yükleme tamamlandı", text + "\n\nŞimdi yeniden giriş yapın.")
        self.ctx.main.logout(after_restore=True)

    def _restore_selected(self) -> None:
        r = self.table.current_row()
        if r and self.svc.is_admin:
            self._restore(r["path"])

    def _restore_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Yedek dosyası seçin", str(self.ctx.backups.backup_dir),
                                              "Kasa Takip yedeği (*.kdb *.db);;Tüm dosyalar (*)")
        if path:
            self._restore(path)

    def _export_all(self) -> None:
        export_excel(self.ctx, self, f"KasaTakip_Tum_Veriler_{today().isoformat()}.xlsx", self.svc.export_tables())
