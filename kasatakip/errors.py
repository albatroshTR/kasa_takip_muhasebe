"""Kullanıcıya gösterilecek hata türleri."""


class ServiceError(Exception):
    """İş kuralı hatası; mesajı doğrudan kullanıcıya gösterilir."""


class PermissionDenied(ServiceError):
    pass


class AuthError(ServiceError):
    pass
