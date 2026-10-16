"""Windows DPAPI credential file store for new local Nexora profiles.

The vault binds ciphertext to the current Windows user. No plaintext passwords
are written to profile JSON on Windows. On other platforms the original
local_profiles behavior is retained for test/development compatibility.
"""
from __future__ import annotations

import os
from pathlib import Path
import uuid


def is_available():
    return os.name == "nt"


def _folder(json_path):
    return Path(json_path).parent / "credentials"


def _crypt(data: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI só está disponível no Windows.")
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    source = ctypes.create_string_buffer(data)
    inbound = BLOB(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    outbound = BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    method = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    method.restype = wintypes.BOOL
    method.argtypes = (
        [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB),
         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
        if decrypt else
        [ctypes.POINTER(BLOB), wintypes.LPCWSTR, ctypes.POINTER(BLOB),
         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    )
    if not method(ctypes.byref(inbound), None, None, None, None, 0,
                  ctypes.byref(outbound)):
        raise OSError("O Windows não conseguiu proteger ou ler a credencial DPAPI.")
    try:
        return ctypes.string_at(outbound.pbData, outbound.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(outbound.pbData, ctypes.c_void_p))


def write_secret(json_path, account_id: str, password: str) -> str:
    uid = str(uuid.UUID(account_id))
    folder = _folder(json_path)
    folder.mkdir(parents=True, exist_ok=True)
    protected = _crypt(password.encode("utf-8"))
    target = folder / (uid + ".dpapi")
    # Write atomically so interrupted updates cannot truncate a credential.
    import tempfile
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".secret-",
                                     delete=False) as temp:
        tmp = Path(temp.name)
        try:
            temp.write(protected)
            temp.flush()
            os.fsync(temp.fileno())
        finally:
            pass
    try:
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return "dpapi:" + uid


def read_secret(json_path, ref: str) -> str:
    if not ref.startswith("dpapi:"):
        raise ValueError("Referência inválida de credencial.")
    uid = str(uuid.UUID(ref.split(":", 1)[1]))
    return _crypt((_folder(json_path) / (uid + ".dpapi")).read_bytes(), decrypt=True).decode("utf-8")


def delete_secret(json_path, ref):
    if ref and ref.startswith("dpapi:"):
        uid = str(uuid.UUID(ref.split(":", 1)[1]))
        (_folder(json_path) / (uid + ".dpapi")).unlink(missing_ok=True)
