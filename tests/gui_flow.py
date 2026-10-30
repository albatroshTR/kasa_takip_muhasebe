"""Arayüz akış testi: düğmelerin arkasındaki işlemleri (kayıt, kapanış, yedek, rapor, yapıştırma) uçtan uca çalıştırır.
Modal pencereler otomatik yanıtlanır.  Çalıştırma:  python -m tests.gui_flow"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp(prefix="kasatakip_flow_")
os.environ["KASATAKIP_DATA"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QLocale  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog  # noqa: E402

from kasatakip import demo  # noqa: E402
from kasatakip.backup import BackupManager  # noqa: E402
from kasatakip.db import Database  # noqa: E402
from kasatakip.paths import AppPaths  # noqa: E402
from kasatakip.services import Service  # noqa: E402
from kasatakip.util import today  # noqa: E402

CHECKS = 0
OPENED = []


def ok(cond, msg):
    global CHECKS
    if not cond:
        raise AssertionError(msg)
    CHECKS += 1
    print("  ✓", msg)


def main():
    QLocale.setDefault(QLocale(QLocale.Turkish, QLocale.Turkey))
    app = QApplication([])
    app.setStyle("Fusion")
    import kasatakip.ui.context as context
    import kasatakip.ui.widgets as widgets
    from kasatakip.ui import dialogs, login, paste_dialog
    from kasatakip.ui.main_window import MainWindow, apply_app_style

    # ---- modal pencereleri otomatik yanıtla
    widgets._box = lambda parent, icon, title, text, buttons: buttons[0][2]   # ilk düğme (Evet/Tamam)
    context.open_path = lambda p: OPENED.append(str(p))
    QDialog.exec = lambda self: 0                                              # betiklenmeyen pencereler: İptal
    save_target = {"path": str(Path(TMP) / "cikti.xlsx")}
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (save_target["path"], ""))

    paths = AppPaths()
    paths.ensure()
    db = Database(paths.db_path)
    svc = Service(db)

    # ---- ilk kurulum ekranı
    from kasatakip.ui.context import AppContext
    ctx = AppContext(svc=svc, db=db, paths=paths, backups=BackupManager(db, svc, paths.backup_dir))
    # Windows koyu temadayken Qt koyu palet verir (beyaz yazı); stil sayfası tablo zeminini beyaz yapar ->
    # palet açık temaya sabitlenmezse tablolarda beyaz zemine beyaz yazı çıkar
    from PySide6.QtGui import QColor, QPalette
    from kasatakip.ui.theme import C
    koyu = QPalette()
    for rol, renk in ((QPalette.Window, "#202020"), (QPalette.WindowText, "#FFFFFF"), (QPalette.Base, "#2D2D2D"),
                      (QPalette.Text, "#FFFFFF"), (QPalette.Button, "#3C3C3C"), (QPalette.ButtonText, "#FFFFFF")):
        koyu.setColor(rol, QColor(renk))
    app.setPalette(koyu)
    apply_app_style(app, "normal")
    pal = app.palette()
    ok(pal.color(QPalette.Text).name() == QColor(C["text"]).name() and pal.color(QPalette.Base).name() == "#ffffff"
       and pal.color(QPalette.WindowText).name() == QColor(C["text"]).name(),
       "koyu Windows temasında da açık palet: yazılar koyu, tablo zemini beyaz")
    print("İlk kurulum")
    sd = login.SetupDialog(ctx)
    sd.firma.setText("Test Kargo")
    sd.full.setText("Ali Veli")
    sd.user.setText("yonetici")
    sd.pw.setText("1234")
    sd.pw2.setText("12345")
    sd._ok()
    ok(sd.err.isVisibleTo(sd) and "aynı değil" in sd.err.text(), "kurulum: şifre uyuşmazlığı yakalandı")
    sd.pw2.setText("1234")
    sd._ok()
    ok(svc.users_exist() and svc.user["kullanici_adi"] == "yonetici", "kurulum: yönetici oluşturuldu")
    svc.logout()

    print("Giriş")
    ld = login.LoginDialog(ctx)
    ld.user.setText("yonetici")
    ld.pw.setText("yanlis")
    ld._login()
    ok("hatalı" in ld.err.text(), "giriş: hatalı şifre mesajı")
    ld.pw.setText("1234")
    ld._login()
    ok(svc.user is not None, "giriş: başarılı")

    # demo verisini mevcut kuruluma ekle (kullanıcılar zaten var)
    demo.populate(svc)
    svc.user = None
    svc.login("yonetici", "1234")

    class Ctrl:
        def __init__(self):
            self.logged_out = False

        def logout(self, after_restore=False):
            self.logged_out = True

        def on_main_closing(self):
            pass

    ctrl = Ctrl()
    win = MainWindow(ctx, ctrl)
    win.resize(1400, 880)
    win.show()
    for k in list(win.pages):
        win.navigate(k)
    ok(len(win.pages) == 12, "yönetici tüm sayfaları görüyor")

    # ---- Günlük kasa: hızlı kayıt
    print("Günlük kasa")
    kp = win.pages["kasa"]
    win.navigate("kasa")
    kp.set_date(today())
    n0 = len(svc.movements(start=today(), end=today()))
    kp.q_tur.set_value("GIDER", emit=True)
    kp.q_tutar.setText("abc")
    kp._quick_add()
    ok("anlaşılamadı" in kp.q_msg.text(), "hızlı kayıt: hatalı tutar yakalandı")
    kp.q_tutar.setText("1.234,50")
    kp.q_aciklama.setText("Test gider")
    kp._quick_add()
    rows = svc.movements(start=today(), end=today())
    ok(len(rows) == n0 + 1 and any(r["tutar"] == 123450 and r["tur"] == "GIDER" for r in rows),
       "hızlı kayıt: gider eklendi")
    ok(kp.table.model_.rowCount() == n0 + 1, "hızlı kayıt: tablo yenilendi")

    # ---- hareket penceresi
    print("Hareket penceresi")
    md = dialogs.MovementDialog(ctx, win, "GELIR", defaults={"tarih": today()})
    md.tutar.setText("2500")
    md.aciklama.setText("Pencereden gelir")
    md._save(again=True)
    ok("Kaydedildi" in md.msg.text(), "pencere: kaydet ve yeni")
    md.seg.set_value("VIRMAN", emit=True)
    md.tutar.setText("100")
    combo_idx = md.hedef.findText("Havale")
    md.hedef.setCurrentIndex(combo_idx)
    md._save(again=False)
    ok(md.result() == QDialog.Accepted, "pencere: virman kaydedildi")
    v = [r for r in svc.movements(start=today(), end=today()) if r["tur"] == "VIRMAN" and r["tutar"] == 10000]
    ok(len(v) == 1 and v[0]["hedef_odeme"] == "Havale", "virman hedefi doğru")
    md = dialogs.MovementDialog(ctx, win, "CARI_BORC")
    md.tutar.setText("50")
    md._save()
    ok("Cari seçin" in md.msg.text(), "cari fişi: cari zorunlu")
    md.cari.setEditText("Olmayan Firma")
    md._save()
    ok("bulunamadı" in md.msg.text(), "cari fişi: geçersiz cari yakalandı")
    cari0 = svc.caris()[0]
    md.cari.set_current_id(cari0["id"])
    md._save()
    ok(md.result() == QDialog.Accepted, "cari fişi kaydedildi")
    m = svc.movements(start=today(), end=today(), tur="GELIR")[-1]
    ed = dialogs.MovementDialog(ctx, win, movement=svc.get_movement(m["id"]))
    ed.tutar.setText("9.999,99")
    ed._save()
    ok(svc.get_movement(m["id"])["tutar"] == 999999, "düzenleme kaydedildi")
    ed = dialogs.MovementDialog(ctx, win, movement=svc.get_movement(m["id"]))
    ed._delete()
    ok(not [x for x in svc.movements() if x["id"] == m["id"]], "pencereden silme")

    # ---- çoğalt / sil (bağlam işlemleri)
    r = svc.movements(start=today(), end=today(), tur="GIDER")[0]
    context.duplicate_movement(ctx, win, r, today())       # pencere açılır (İptal)
    context.delete_movements(ctx, win, [r])
    ok(not [x for x in svc.movements() if x["id"] == r["id"]], "seçili kayıt silindi")

    # ---- gün kapanışı
    print("Gün kapanışı")
    y = today() - dt.timedelta(days=1)
    kp.set_date(y)
    orig = dialogs.CloseDayDialog.exec

    def close_exec(self):
        self._fill_system()
        self.note.setPlainText("Test kapanış")
        self._ok()
        return self.result() or 1
    dialogs.CloseDayDialog.exec = close_exec
    nb = len(ctx.backups.list_backups())
    kp._close_day()
    dialogs.CloseDayDialog.exec = orig
    ok(svc.is_locked(y), "gün kapatıldı")
    ok(len(ctx.backups.list_backups()) > nb, "kapanışta yedek alındı")
    ok(any("Kasa_" in p for p in OPENED), "kapanışta PDF oluşturuldu")
    ok(kp.b_open.isVisible() and not kp.q_add.isEnabled(), "kapalı günde giriş kilitli")
    kp._reopen()
    ok(not svc.is_locked(y), "gün yeniden açıldı")

    # ---- yapıştırma
    print("Excel'den yapıştırma")
    text = ("Tarih\tTür\tKategori\tÖdeme\tTutar\tAçıklama\tCari\n"
            f"{today():%d.%m.%Y}\tGelir\tKargo Geliri\tNakit\t1.250,50\tYapıştırma 1\t\n"
            f"{today():%d.%m.%Y}\tGider\tYeni Kalem\tNakit\t300\tYapıştırma 2\tYeni Tedarikçi Ltd\n")
    pdlg = paste_dialog.PasteImportDialog(ctx, win, "hareket", today(), text=text)
    ok(pdlg.save_btn.text().startswith("1 "), "yapıştırma: 1 geçerli, 1 hatalı")
    pdlg.new_cat.setChecked(True)
    pdlg.new_cari.setChecked(True)
    ok(pdlg.save_btn.text().startswith("2 "), "yapıştırma: seçeneklerle 2 geçerli")
    pdlg._save()
    ok(pdlg.saved_count == 2 and any(c["unvan"] == "Yeni Tedarikçi Ltd" for c in svc.caris()),
       "yapıştırma: kaydedildi, yeni cari açıldı")
    cdlg = paste_dialog.PasteImportDialog(ctx, win, "cari",
                                          text="Ünvan\tTelefon\tBakiye\nYapıştır Cari A\t0332 1\t1.000\n")
    cdlg._save()
    ok(any(c["unvan"] == "Yapıştır Cari A" and c["bakiye"] == 100000 for c in svc.caris()), "cari yapıştırma")
    QGuiApplication.clipboard().setText("")
    pdlg = paste_dialog.PasteImportDialog(ctx, win, "hareket", today())
    pdlg.paste_clipboard()   # boş pano: bilgi mesajı
    ok(not pdlg.save_btn.isEnabled(), "boş pano: kaydet kapalı")

    # ---- hareketler sayfası
    print("Hareketler")
    hp = win.pages["hareketler"]
    win.navigate("hareketler")
    hp.search.setText("Yapıştırma")
    hp.safe_refresh()
    ok(hp.table.model_.rowCount() == 2, "arama filtresi")
    hp.search.setText("")
    hp.tur.setCurrentIndex(1)   # Gelir
    hp.safe_refresh()
    ok(all(r["tur"] == "GELIR" for r in hp.table.rows()), "tür filtresi")
    save_target["path"] = str(Path(TMP) / "hareketler.xlsx")
    hp._export()
    ok(Path(save_target["path"]).exists(), "hareketler Excel'e aktarıldı")
    hp.table.selectRow(0)
    hp.table.copy_selection(headers=True)
    ok(QGuiApplication.clipboard().text().startswith("Tarih\t"), "tablodan kopyalama")
    hp._clear_filters()

    # ---- cariler
    print("Cariler")
    cp = win.pages["cariler"]
    win.navigate("cariler")
    cp.list.selectRow(0)
    cp._selected()
    ok(cp.cur is not None and cp.ekstre.model_.rowCount() >= 1, "cari seçildi, ekstre yüklendi")
    n_open = len(OPENED)
    cp._ekstre_pdf()
    cp._list_pdf()
    ok(len(OPENED) == n_open + 2, "ekstre ve cari listesi PDF")
    save_target["path"] = str(Path(TMP) / "ekstre.xlsx")
    cp._ekstre_excel()
    ok(Path(save_target["path"]).exists(), "ekstre Excel")
    widgets.combo_select(cp.period.preset, "Tüm Zamanlar")
    first = cp.ekstre.rows()[0]
    ok(first["islem"] == "Devir" and not first["tarih"].startswith("2000"),
       f"tüm zamanlar: ekstre devri ilk kayıt tarihinde ({first['tarih']})")
    save_target["path"] = str(Path(TMP) / "ekstre_tum.xlsx")
    cp._ekstre_excel()
    from openpyxl import load_workbook
    title = load_workbook(save_target["path"]).active["A1"].value
    ok("2000" not in title and "2099" not in title, f"tüm zamanlar: ekstre Excel başlığı ({title})")
    widgets.combo_select(cp.period.preset, "Bu Yıl")
    cd = dialogs.CariDialog(ctx, win)
    cd.unvan.setText("Pencere Cari")
    cd.acilis.setText("750")
    cd.acilis_taraf.set_value("ALACAK")
    cd._save()
    pc = [c for c in svc.caris() if c["unvan"] == "Pencere Cari"]
    ok(pc and pc[0]["bakiye"] == -75000, "cari penceresi: açılış alacak bakiyesi")
    cp.cur = {"id": pc[0]["id"]}
    cp._delete()
    ok(not any(c["unvan"] == "Pencere Cari" for c in svc.caris()), "hareketli cari pasif yapıldı")

    # ---- analiz
    print("Analiz")
    ap = win.pages["analiz"]
    win.navigate("analiz")
    for i in range(ap.group.count()):
        ap.group.setCurrentIndex(i)
    for i in range(ap.compare.count()):
        ap.compare.setCurrentIndex(i)
    ap.compare.setCurrentIndex(0)
    for i, t in enumerate(ap.tabs):
        ap.tabbar.setCurrentIndex(i)
        ok(not t.dirty, f"analiz sekmesi yüklendi: {t.name}")
    ozet = ap.tabs[0]
    ok(ozet.k["gelir"].value.text().endswith("₺") and ozet.ins.count() > 3, "genel bakış göstergeleri ve yorumlar")
    kt = ap.tabs[2]
    ap.show_tab(kt.name)
    kt.tur.set_value("GELIR", emit=True)
    ok(kt.table.model_.rowCount() >= 2 and kt.pivot.model_.columnCount() > 3, "kategori tablosu ve özet tablo")
    from PySide6.QtCore import Qt as _Qt
    for order in (_Qt.AscendingOrder, _Qt.DescendingOrder):
        kt.table.sortByColumn(2, order)
        ok(kt.table.visible_rows()[-1]["ad"] == "TOPLAM", "sıralamada TOPLAM satırı en altta kalır")
    ct = ap.tabs[4]
    ap.show_tab(ct.name)
    ct.side.set_value("borc", emit=True)
    ct.side.set_value("alacak", emit=True)
    ok(ct.aging_t.model_.rowCount() >= 2, "yaşlandırma tablosu")
    zt = ap.tabs[5]
    ap.show_tab(zt.name)
    for m in ("gider", "net", "adet", "gelir"):
        zt.metric_sel.set_value(m, emit=True)
    ok(zt.week_t.model_.rowCount() == 7, "haftanın günleri tablosu")
    zt.top_in.selectRow(0)
    zt.top_in._emit_activated()
    app.processEvents()
    ok(win.current_page() is win.pages["kasa"], "en yüksek gün çift tıklama -> kasa")
    win.navigate("analiz")
    yt = ap.tabs[6]
    ap.show_tab(yt.name)
    for m in ("gider", "net"):
        yt.metric_sel.set_value(m, emit=True)
    ok(yt.table.model_.rowCount() == 13, "yıllık tablo (12 ay + toplam)")
    ct = ap.tabs[4]
    ap.show_tab(ct.name)
    ct.mizan.selectRow(0)
    ct.mizan._emit_activated()
    app.processEvents()
    ok(win.current_page() is win.pages["cariler"] and win.pages["cariler"].cur is not None, "mizandan cari açıldı")
    win.navigate("analiz")
    ap._pdf()
    save_target["path"] = str(Path(TMP) / "analiz.xlsx")
    ap._excel()
    ok(Path(save_target["path"]).exists() and any("Donem_Raporu" in p for p in OPENED), "analiz PDF ve Excel")

    # ---- raporlar
    print("Raporlar")
    from kasatakip import reports as reps
    rp = win.pages["raporlar"]
    win.navigate("raporlar")
    ok(rp.rd is not None and rp.rd.key == "gunluk_kasa" and rp.rep is not None, "raporlar: varsayılan rapor önizlendi")
    for rd in reps.available(svc):
        rp.select_report(rd.key)
        if rd.key == "cari_ekstre":
            ok(rp.rep is None and not rp.b_pdf.isEnabled(), "cari ekstre: cari seçilmeden PDF kapalı")
            box = [rp.flow.itemAt(i).widget() for i in range(rp.flow.count())
                   if rp.flow.itemAt(i).widget().property("param_key") == "cari"][0]
            from kasatakip.ui.widgets import SearchCombo
            sc = box.findChild(SearchCombo)
            sc.set_current_id(svc.caris()[0]["id"])
            rp._preview()
        ok(rp.rep is not None and rp.view.lay.count() > 1, f"rapor önizlemesi: {rd.title}")
        n_open = len(OPENED)
        rp._pdf()
        ok(len(OPENED) == n_open + 1 and OPENED[-1].endswith(".pdf"), f"rapor PDF: {rd.title}")
        save_target["path"] = str(Path(TMP) / f"rapor_{rd.key}.xlsx")
        rp._excel()
        ok(Path(save_target["path"]).exists(), f"rapor Excel: {rd.title}")
    ok(rp.recent.count() > 5, "son oluşturulan raporlar listelendi")
    rp.select_report("kasa_defteri")
    rp.view.activated.emit("gun", today().isoformat())
    ok(win.current_page() is win.pages["kasa"], "rapordan güne gitme")
    win.navigate("raporlar")

    # ---- tanımlar
    print("Tanımlar")
    tp = win.pages["tanimlar"]
    win.navigate("tanimlar")
    orig_text_exec = dialogs.TextDialog.exec

    def text_exec(self):
        self.edit.setText("Temizlik")
        return 1
    import kasatakip.ui.pages.tanimlar as tanimlar_mod
    tanimlar_mod.TextDialog.exec = text_exec
    tp.gider._add()
    tanimlar_mod.TextDialog.exec = orig_text_exec
    ok(any(c["ad"] == "Temizlik" for c in svc.categories("GIDER")), "kategori eklendi")
    tp.gider.table.select_where("ad", "Temizlik")
    tp.gider._move(-1)
    tp.gider._toggle()
    ok(not [c for c in svc.categories("GIDER") if c["ad"] == "Temizlik"][0]["aktif"], "kategori pasif")
    tp.gider.table.select_where("ad", "Temizlik")
    tp.gider._delete()
    ok(not any(c["ad"] == "Temizlik" for c in svc.categories("GIDER")), "kullanılmayan kategori silindi")
    pt = dialogs.PaymentTypeDialog(ctx, win)
    pt.ad.setText("Banka - Ziraat")
    pt.acilis.setText("10.000")
    pt._save()
    ok(any(p["ad"] == "Banka - Ziraat" and p["acilis_bakiye"] == 1000000 for p in svc.payment_types()),
       "ödeme tipi eklendi")

    # ---- kullanıcılar
    print("Kullanıcılar")
    ud = dialogs.UserDialog(ctx, win)
    ud.name.setText("muhasebe")
    ud.full.setText("Muhasebe Kullanıcısı")
    ud.pw1.setText("1111")
    ud.pw2.setText("1111")
    ud._save()
    u = [x for x in svc.list_users() if x["kullanici_adi"] == "muhasebe"]
    ok(u and u[0]["rol"] == "kullanici", "kullanıcı eklendi")
    rd = dialogs.PasswordDialog(ctx, win, "reset", u[0])
    rd.pw1.setText("2222")
    rd.pw2.setText("2222")
    rd._save()
    ok(rd.result() == QDialog.Accepted, "şifre sıfırlandı")
    chg = dialogs.PasswordDialog(ctx, win, "change")
    chg.old.setText("yanlis")
    chg.pw1.setText("5555")
    chg.pw2.setText("5555")
    chg._save()
    ok("hatalı" in chg.msg.text(), "kendi şifresi: eski şifre kontrolü")

    # ---- ayarlar
    print("Ayarlar")
    sp = win.pages["ayarlar"]
    win.navigate("ayarlar")
    sp.f["adi"].setText("Test Kargo Konya")
    sp._save_firma()
    ok(svc.firma()["adi"] == "Test Kargo Konya" and "Test Kargo Konya" in win.windowTitle(), "firma bilgisi")
    sp._font_changed("buyuk")
    sp._font_changed("normal")
    sp._save_imza(False)
    ok(svc.setting("pdf_imza") == "0", "imza ayarı")
    dialogs.RecoveryKeyDialog.exec = lambda self: 1
    import kasatakip.ui.pages.ayarlar as ayarlar_mod
    ayarlar_mod.RecoveryKeyDialog.exec = lambda self: 1
    sp._new_key()
    ok(any(a["konu"] == "Ayarlar" and "kurtarma" in a["detay"] for a in svc.audit(today(), today())),
       "kurtarma anahtarı yenilendi")

    # ---- işlem geçmişi
    print("İşlem geçmişi")
    gp = win.pages["gecmis"]
    win.navigate("gecmis")
    ok(gp.table.model_.rowCount() > 10, "işlem geçmişi listelendi")
    save_target["path"] = str(Path(TMP) / "gecmis.xlsx")
    gp._export()
    ok(Path(save_target["path"]).exists(), "işlem geçmişi Excel")

    # ---- yedekleme ve geri yükleme
    print("Yedekleme")
    yp = win.pages["yedekleme"]
    win.navigate("yedekleme")
    nb = len(ctx.backups.list_backups())
    yp._backup_now()
    ok(len(ctx.backups.list_backups()) > nb, "elle yedek alındı")
    win.navigate("yedekleme")
    manual = [b for b in ctx.backups.list_backups() if b["tur"] == "manuel"][0]
    count = len(svc.movements())
    svc.add_movement({"tarih": today(), "tur": "GELIR", "kategori_id": svc.categories("GELIR")[0]["id"],
                      "odeme_tipi_id": svc.payment_types()[0]["id"], "tutar": 777})
    yp.table.select_where("path", manual["path"])
    yp._restore_selected()
    ok(len(svc.movements()) == count and ctrl.logged_out, "geri yükleme ve yeniden giriş istendi")
    save_target["path"] = str(Path(TMP) / "tum.xlsx")
    yp._export_all()
    ok(Path(save_target["path"]).exists(), "tüm veriler Excel'e aktarıldı")

    # ---- ana pencere kısayol işlevleri
    win._new("GELIR")
    win.open_kasa(today().isoformat())
    win._refresh_current()
    win._help()
    ok(win.current_page() is kp, "kasaya git")

    # ---- kullanıcı rolü: kısıtlar
    print("Kullanıcı rolü")
    svc.logout()
    svc.login("kasiyer", "1234")
    win2 = MainWindow(ctx, ctrl)
    win2.show()
    ok("tanimlar" not in win2.pages and "kullanicilar" not in win2.pages, "kullanıcı yönetim sayfalarını görmez")
    ok("raporlar" in win2.pages and "analiz" in win2.pages, "kullanıcı analiz ve raporları görür")
    rp2 = win2.pages["raporlar"]
    ok(all(rp2.list.item(i).data(0x0100) != "islem_gecmisi" for i in range(rp2.list.count())),
       "kullanıcı işlem geçmişi raporunu görmez")
    k2 = win2.pages["kasa"]
    win2.navigate("kasa")
    k2.set_date(today() - dt.timedelta(days=3))
    ok(not k2.b_open.isEnabled() or not k2.b_open.isVisible(), "kullanıcı günü açamaz")
    win2.navigate("yedekleme")
    ok(not win2.pages["yedekleme"].b_restore.isEnabled(), "kullanıcı geri yükleme yapamaz")
    print(f"\nTAMAM: {CHECKS} kontrol geçti")


if __name__ == "__main__":
    main()
