"""Kullanıcılar (yalnız yönetici)."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from ...errors import ServiceError
from ..dialogs import PasswordDialog, UserDialog
from ..theme import C
from ..widgets import Card, Col, DataTable, ask, button, info, muted, warn
from .base import Page


class KullanicilarPage(Page):
    title = "Kullanıcılar"
    subtitle = "Programa kimlerin, hangi yetkiyle gireceği"

    def __init__(self, ctx):
        super().__init__(ctx)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        card = Card("Kullanıcı Listesi")
        tb = QHBoxLayout()
        tb.addWidget(button("Yeni Kullanıcı", "plus", "primary", callback=self._new))
        self.b_edit = button("Düzenle", "edit", callback=self._edit)
        self.b_reset = button("Şifre Sıfırla", "key", callback=self._reset)
        tb.addWidget(self.b_edit)
        tb.addWidget(self.b_reset)
        tb.addStretch(1)
        self.b_devret = button("Sahipliği Devret", "shield", tip="Program sahipliğini seçili aktif yöneticiye devreder",
                               callback=self._devret)
        tb.addWidget(self.b_devret)
        card.add(tb)
        self.table = DataTable([
            Col("kullanici_adi", "Kullanıcı Adı", width=160, bold=True),
            Col("ad_soyad", "Ad Soyad", stretch=True),
            Col("rol_adi", "Rol", width=150, color=lambda r: C["primary"] if r["rol"] == "yonetici" else None,
                fmt=lambda v, r: f"{v} · Sahip" if r["sahip"] else v),
            Col("durum", "Durum", width=80, color=lambda r: C["gelir"] if r["aktif"] else C["faint"]),
            Col("son_giris", "Son Giriş", "datetime", 150),
            Col("olusturma", "Oluşturma", "datetime", 150),
        ], multi=False)
        self.table.rowActivated.connect(lambda r: self._edit())
        self.table.selectionChangedRows.connect(self._update_buttons)
        card.add(self.table, 1)
        card.add(muted("Yönetici: tüm işlemler. Kullanıcı: kayıt girer/düzenler, günü kapatır, rapor alır; tanımları, "
                       "kullanıcıları, ayarları ve kapanmış günleri değiştiremez. Yönetici şifresi unutulursa giriş "
                       "ekranındaki 'Şifremi unuttum' ile kurtarma anahtarı kullanılır. Şifreli veriler her aktif "
                       "kullanıcının kendi şifresiyle açılır. Şifre değişince ya da kullanıcı pasif yapılınca eski "
                       "şifre yeni dosyanın kilidini açmaz; ancak değişiklikten önce alınmış bir yedekle birlikte "
                       "eski şifre, veri anahtarı aynı kaldığı için sonraki kayıtları da açabilir. Pasif kullanıcı "
                       "yeniden aktif yapılırsa şifresini sıfırlayın. Sahip: ilk kurulumda açılan yönetici; "
                       "hesabını yalnızca kendisi değiştirir, yeni kurtarma anahtarını yalnızca o oluşturur, "
                       "sahipliği başka bir aktif yöneticiye devredebilir.", small=True))
        root.addWidget(card)

    def refresh(self) -> None:
        self.table.set_rows(self.svc.list_users())
        self._update_buttons()

    def _sahip_kilitli(self, r, sifre: bool = False) -> bool:
        """Sahibin hesabını yalnızca sahip değiştirir (kural services.py'de de denetlenir). Şifreli dosyada sahibin
        kilidi yoksa şifresini başka bir yönetici de sıfırlayabilir."""
        return bool(r and r["sahip"] and not self.svc.is_sahip and not (sifre and self.svc.sahip_kilitsiz()))

    def _update_buttons(self) -> None:
        r = self.table.current_row()
        for b, sifre in ((self.b_edit, False), (self.b_reset, True)):
            kilitli = self._sahip_kilitli(r, sifre)
            b.setEnabled(not kilitli)
            b.setToolTip("Program sahibinin hesabını yalnızca sahibin kendisi değiştirebilir." if kilitli else "")
        self.b_devret.setVisible(self.svc.is_sahip)
        self.b_devret.setEnabled(bool(r and not r["sahip"] and r["rol"] == "yonetici" and r["aktif"]))

    def _devret(self) -> None:
        r = self.table.current_row()
        if not r or not ask(self, "Sahipliği Devret",
                            f"Program sahipliği {r['kullanici_adi']} kullanıcısına devredilsin mi?\n\nBundan sonra "
                            "sahibin hesabını, yeni kurtarma anahtarını ve sahipliği yalnızca o yönetebilir."):
            return
        try:
            self.svc.sahipligi_devret(r["id"])
        except ServiceError as e:
            warn(self, "Devredilemedi", str(e))
            return
        self.ctx.bus.changed.emit("kullanici")
        self.ctx.status(f"Program sahipliği {r['kullanici_adi']} kullanıcısına devredildi.")
        self.refresh()

    def _new(self) -> None:
        UserDialog(self.ctx, self).exec()

    def _edit(self) -> None:
        r = self.table.current_row()
        if r and not self._sahip_kilitli(r):
            UserDialog(self.ctx, self, r).exec()

    def _reset(self) -> None:
        r = self.table.current_row()
        if not r or self._sahip_kilitli(r, sifre=True):
            return
        if r["id"] == self.svc.user["id"]:
            PasswordDialog(self.ctx, self, "change").exec()
            return
        if PasswordDialog(self.ctx, self, "reset", r).exec():
            info(self, "Şifre sıfırlandı", f"{r['kullanici_adi']} bir sonraki girişinde yeni şifre belirleyecek.")
            self.ctx.bus.changed.emit("kullanici")
