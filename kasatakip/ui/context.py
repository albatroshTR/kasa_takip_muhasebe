"""Uygulama bağlamı ve ekranlar arası ortak işlemler."""
from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog

from .. import excel, reports_pdf
from ..errors import ServiceError
from ..util import fmt_date, fmt_money, today

log = logging.getLogger("kasatakip")


class Bus(QObject):
    """Veri değiştiğinde ekranların kendini yenilemesi için yayın kanalı."""
    changed = Signal(str)


@dataclass
class AppContext:
    svc: object
    db: object
    paths: object
    backups: object
    bus: Bus = field(default_factory=Bus)
    main: object = None

    # ------------------------------------------------------------ yardımcılar
    def status(self, msg: str, ms: int = 7000) -> None:
        if self.main is not None:
            self.main.statusBar().showMessage(msg, ms)

    def report_dir(self) -> Path:
        v = self.svc.setting("rapor_klasoru", "").strip()
        return Path(v) if v else self.paths.default_report_dir

    def user_label(self) -> str:
        u = self.svc.user or {}
        return u.get("ad_soyad") or u.get("kullanici_adi") or ""


@contextmanager
def busy(ctx=None, msg: str = ""):
    """Uzun işlemlerde (büyük PDF / Excel) bekleme imleci ve durum mesajı."""
    QApplication.setOverrideCursor(Qt.WaitCursor)
    if ctx is not None and msg:
        ctx.status(msg, 0)
        QApplication.processEvents()
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()
        sb = ctx.main.statusBar() if (ctx is not None and msg and ctx.main is not None) else None
        if sb is not None and sb.currentMessage() == msg:
            sb.clearMessage()


def open_path(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def writable_path(path: Path) -> Path:
    """PDF bir görüntüleyicide açıksa (Windows dosyayı kilitler) yeni bir ad üretir."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cand, i = path, 2
    while True:
        try:
            if cand.exists():
                with open(cand, "ab"):
                    pass
            return cand
        except PermissionError:
            cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
            i += 1
            if i > 50:
                return cand


def safe_name(text: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if ch in bad else ch for ch in (text or "")).strip().rstrip(".")
    return out[:80] or "rapor"


# ------------------------------------------------------------ hareket işlemleri
def new_movement(ctx, parent, tur: str = "GELIR", defaults: dict | None = None) -> None:
    from .dialogs import MovementDialog
    MovementDialog(ctx, parent, tur=tur, defaults=defaults).exec()


def edit_movement(ctx, parent, m: dict) -> None:
    from .dialogs import MovementDialog
    from .widgets import warn
    try:
        fresh = ctx.svc.get_movement(m["id"])
    except ServiceError as e:
        warn(parent, "Kayıt", str(e))
        ctx.bus.changed.emit("hareket")
        return
    MovementDialog(ctx, parent, movement=fresh).exec()


def duplicate_movement(ctx, parent, m: dict, tarih: dt.date | None = None) -> None:
    keys = ("kategori_id", "odeme_tipi_id", "hedef_odeme_tipi_id", "cari_id", "tutar", "aciklama", "belge_no")
    defaults = {k: m.get(k) for k in keys}
    defaults["tarih"] = tarih or today()
    new_movement(ctx, parent, m["tur"], defaults)


def delete_movements(ctx, parent, rows: list[dict]) -> None:
    from .widgets import ask, warn
    if not rows:
        return
    if len(rows) == 1:
        text = f"Bu kayıt silinsin mi?\n\n{ctx.svc.describe(rows[0])}"
    else:
        total = sum(r["tutar"] for r in rows)
        text = f"Seçili {len(rows)} kayıt silinsin mi?\n\nToplam tutar: {fmt_money(total)} ₺"
    if not ask(parent, "Kayıt Sil", text, yes="Sil", danger=True):
        return
    try:
        n = ctx.svc.delete_movements([r["id"] for r in rows])
    except ServiceError as e:
        warn(parent, "Silinemedi", str(e))
        return
    ctx.bus.changed.emit("hareket")
    ctx.status(f"{n} kayıt silindi.")


def paste_import(ctx, parent, mode: str = "hareket", default_date=None, default_tur: str = "GELIR",
                 from_clipboard: bool = False) -> None:
    from PySide6.QtGui import QGuiApplication
    from .paste_dialog import PasteImportDialog
    text = QGuiApplication.clipboard().text() if from_clipboard else None
    dlg = PasteImportDialog(ctx, parent, mode, default_date=default_date, default_tur=default_tur,
                            text=text if text and text.strip() else None)
    if dlg.exec() and dlg.saved_count:
        ctx.status(f"{dlg.saved_count} kayıt eklendi.")


# ------------------------------------------------------------ raporlar
def daily_pdf(ctx, parent, d: dt.date, open_after: bool = True) -> Path | None:
    from .widgets import error
    try:
        rep = ctx.svc.day_report(d)
        path = writable_path(ctx.report_dir() / "Günlük Kasa" / f"Kasa_{d.isoformat()}.pdf")
        imza = ctx.svc.setting("pdf_imza", "1") == "1"
        with busy(ctx, "Günlük kasa raporu hazırlanıyor…"):
            reports_pdf.daily_kasa_pdf(path, rep, ctx.svc.firma(), ctx.user_label(), imza=imza)
    except Exception as e:  # noqa: BLE001
        log.exception("Günlük kasa PDF")
        error(parent, "PDF oluşturulamadı", str(e))
        return None
    ctx.status(f"Günlük kasa raporu kaydedildi: {path}")
    if open_after:
        open_path(path)
    return path


def save_report(ctx, parent, subdir: str, filename: str, builder) -> Path | None:
    """builder(path) PDF'i yazar. Rapor klasörüne kaydedip açar."""
    from .widgets import error
    try:
        path = writable_path(ctx.report_dir() / subdir / filename)
        with busy(ctx, "Rapor hazırlanıyor…"):
            builder(path)
    except Exception as e:  # noqa: BLE001
        log.exception("PDF rapor")
        error(parent, "PDF oluşturulamadı", str(e))
        return None
    ctx.status(f"Rapor kaydedildi: {path}")
    open_path(path)
    return path


def export_excel(ctx, parent, default_name: str, sheets: list[dict]) -> Path | None:
    from .widgets import error
    start = ctx.report_dir() / "Excel"
    start.mkdir(parents=True, exist_ok=True)
    path, _ = QFileDialog.getSaveFileName(parent, "Excel'e Aktar", str(start / safe_name(default_name)),
                                          "Excel dosyası (*.xlsx)")
    if not path:
        return None
    if not path.lower().endswith(".xlsx"):
        path += ".xlsx"
    try:
        with busy(ctx, "Excel dosyası hazırlanıyor…"):
            excel.export_sheets(path, sheets)
    except PermissionError:
        error(parent, "Kaydedilemedi", "Dosya başka bir programda (Excel) açık olabilir. Kapatıp tekrar deneyin.")
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("Excel")
        error(parent, "Kaydedilemedi", str(e))
        return None
    ctx.status(f"Excel dosyası kaydedildi: {path}")
    open_path(path)
    return Path(path)


def report_pdf(ctx, parent, key: str, values: dict, rep=None) -> Path | None:
    """Raporlar bölümündeki bir raporu PDF olarak rapor klasörüne kaydedip açar."""
    from .. import reports
    from .widgets import error, warn
    try:
        rep = rep or reports.build(key, ctx.svc, values)
    except ServiceError as e:
        warn(parent, "Rapor", str(e))
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("Rapor hazırlanamadı")
        error(parent, "Rapor hazırlanamadı", str(e))
        return None
    return save_report(ctx, parent, rep.subdir, f"{safe_name(rep.filename)}.pdf",
                       lambda p: reports.write_pdf(key, ctx.svc, values, p, ctx.svc.firma(), ctx.user_label()))


def export_report_excel(ctx, parent, rep) -> Path | None:
    """Rapor içeriğini (özet + tablolar) Excel'e aktarır."""
    from .widgets import error
    start = ctx.report_dir() / "Excel"
    start.mkdir(parents=True, exist_ok=True)
    path, _ = QFileDialog.getSaveFileName(parent, "Excel'e Aktar", str(start / f"{safe_name(rep.filename)}.xlsx"),
                                          "Excel dosyası (*.xlsx)")
    if not path:
        return None
    if not path.lower().endswith(".xlsx"):
        path += ".xlsx"
    try:
        with busy(ctx, "Excel dosyası hazırlanıyor…"):
            excel.export_report(path, rep)
    except PermissionError:
        error(parent, "Kaydedilemedi", "Dosya başka bir programda (Excel) açık olabilir. Kapatıp tekrar deneyin.")
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("Excel rapor")
        error(parent, "Kaydedilemedi", str(e))
        return None
    ctx.status(f"Excel dosyası kaydedildi: {path}")
    open_path(path)
    return Path(path)


def open_target(ctx, parent, kind: str, key) -> None:
    """Rapor/analiz tablolarında çift tıklanan satırı ilgili ekranda açar."""
    if key is None or ctx.main is None:
        return
    if kind == "hareket":
        edit_movement(ctx, parent, {"id": key})
    elif kind == "cari":
        ctx.main.show_cari(key)
    elif kind == "gun":
        ctx.main.open_kasa(key)


def period_text(s, e) -> str:
    return f"{fmt_date(s)} – {fmt_date(e)}"
