"""Programın internete çıkışını kapatır.

Program hiçbir ağ işlevi kullanmaz: veriler bu bilgisayarda tutulur, raporlar yerel dosya olarak açılır. Yine de
bir kod parçasının ya da kütüphanenin bağlantı kurmaya çalışması ihtimaline karşı açılışta Python'un ağ işlevleri
devre dışı bırakılır: ad çözümleme (DNS) ve bağlantı denemeleri hata verir.
İşletim sistemi düzeyindeki engel (Windows Güvenlik Duvarı kuralı) kurulumda eklenir (kurulum.iss).
"""
from __future__ import annotations

import socket

MESAJ = "Kasa Takip internete bağlanmaz; programın ağ erişimi kapalıdır."
_SOCKET_METOTLARI = ("connect", "connect_ex", "sendto", "sendmsg")
_MODUL_ISLEVLERI = ("create_connection", "getaddrinfo", "getnameinfo", "gethostbyname", "gethostbyname_ex",
                    "gethostbyaddr")
_kurulu = False


class AgErisimiKapali(OSError):
    pass


def _engel(*_a, **_k):
    raise AgErisimiKapali(MESAJ)


def install() -> None:
    """Python'un ağ işlevlerini kapatır (bir kez çağrılması yeterli; tekrar çağrılabilir)."""
    global _kurulu
    if _kurulu:
        return
    for ad in _SOCKET_METOTLARI:
        if hasattr(socket.socket, ad):
            setattr(socket.socket, ad, _engel)
    for ad in _MODUL_ISLEVLERI:
        if hasattr(socket, ad):
            setattr(socket, ad, _engel)
    _kurulu = True


def active() -> bool:
    return _kurulu
