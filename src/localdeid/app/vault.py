"""Encrypted session vault (SPEC 4.6).

One file per session in `<root>/sessions/`. Each session has its own random
256-bit data key, wrapped (AES-GCM) by a master key that lives in the macOS
Keychain (non-synchronizing, this device only). The file holds a plaintext
JSON header (format, ids, state, times, wrapped key, nonces) and two
ciphertexts: `meta` (what the History list shows) and `body` (the session).
Both use the header as authenticated data, so any change to the header or the
ciphertext fails to decrypt. Deleting the file deletes the wrapped key, and so
the session. Writes are crash-safe (temp file, fsync, atomic rename).

Sessions expire after 30 days, with a warning from day 27, except a session
that is waiting for a draft (SPEC 4.6), which only warns.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "ldv1"
STATES = ("check", "waiting", "done")  # Needs check, Waiting for draft, Done (SPEC 5.2)
EXPIRE_DAYS = 30
WARN_DAYS = 27
DAY = 86400.0
_NAME = re.compile(r"^[A-Z0-9]{4}-[0-9a-f]{32}\.ldv$")


class VaultError(RuntimeError):
    pass


class KeyStore(Protocol):
    def get_or_create(self) -> bytes: ...
    def delete(self) -> None: ...


class MemoryKeyStore:
    """For tests: a master key held in memory."""

    def __init__(self) -> None:
        self.key: bytes | None = None

    def get_or_create(self) -> bytes:
        if self.key is None:
            self.key = AESGCM.generate_key(256)
        return self.key

    def delete(self) -> None:
        self.key = None


class KeychainKeyStore:
    """The master key as a generic password in the login Keychain, readable only
    while the Mac is unlocked, never synchronized to iCloud (SPEC 4.6)."""

    def __init__(self, service: str = "com.mrinoy.localdeid", account: str = "vault-master-key"):
        self.service, self.account = service, account

    def _query(self) -> dict:
        import Security as S

        return {S.kSecClass: S.kSecClassGenericPassword, S.kSecAttrService: self.service,
                S.kSecAttrAccount: self.account, S.kSecAttrSynchronizable: False}

    def get_or_create(self) -> bytes:
        import Security as S
        from Foundation import NSData

        q = self._query()
        q[S.kSecReturnData] = True
        q[S.kSecMatchLimit] = S.kSecMatchLimitOne
        status, data = S.SecItemCopyMatching(q, None)
        if status == 0 and data is not None:
            key = bytes(data)
            if len(key) != 32:
                raise VaultError("keychain-key-invalid")
            return key
        if status != S.errSecItemNotFound:
            raise VaultError(f"keychain-read-{status}")
        key = AESGCM.generate_key(256)
        add = self._query()
        add[S.kSecValueData] = NSData.dataWithBytes_length_(key, len(key))
        add[S.kSecAttrAccessible] = S.kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        status, _ = S.SecItemAdd(add, None)
        if status != 0:
            raise VaultError(f"keychain-write-{status}")
        return key

    def delete(self) -> None:
        import Security as S

        status = S.SecItemDelete(self._query())
        if status not in (0, S.errSecItemNotFound):
            raise VaultError(f"keychain-delete-{status}")  # the key is still there: say so


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def _exclude_from_backup(path: Path) -> None:
    """Time Machine and iCloud skip this folder (NSURLIsExcludedFromBackupKey)."""
    try:
        from Foundation import NSURL, NSURLIsExcludedFromBackupKey

        NSURL.fileURLWithPath_(str(path)).setResourceValue_forKey_error_(True, NSURLIsExcludedFromBackupKey, None)
    except Exception:  # noqa: BLE001 (not on macOS / no PyObjC: tests)
        pass


@dataclass
class SessionHeader:
    id: str
    ref: str
    state: str
    created: float
    updated: float
    meta: dict = field(default_factory=dict)
    days_left: float | None = None


class Vault:
    def __init__(self, root: Path, keystore: KeyStore, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.dir = self.root / "sessions"
        self.tmp = self.root / "tmp"
        self.keystore = keystore
        self.clock = clock
        self._master: bytes | None = None
        self._dirs()

    def _dirs(self) -> None:
        """Create the folders (again after "Delete everything"): private, no backups."""
        if self.dir.is_dir() and self.tmp.is_dir():
            return
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        _exclude_from_backup(self.root)

    # ------------------------------------------------------------------ keys

    def _mk(self) -> bytes:
        if self._master is None:
            self._master = self.keystore.get_or_create()
        return self._master

    # ------------------------------------------------------------------ files

    def _path(self, sid: str, ref: str) -> Path:
        return self.dir / f"{ref}-{sid}.ldv"

    def _find(self, sid: str) -> Path:
        """The session's file. A crash between writing a renamed file (new ref) and
        removing the old one leaves two: the newest save wins, the other goes."""
        paths = list(self.dir.glob(f"*-{sid}.ldv"))
        if not paths:
            raise VaultError("no-such-session")
        if len(paths) == 1:
            return paths[0]
        dated = []
        for p in paths:
            try:
                dated.append((self._header(p)["updated"], p))
            except (VaultError, KeyError):
                continue
        if not dated:
            return paths[0]
        dated.sort(key=lambda x: x[0])
        for _, p in dated[:-1]:
            p.unlink(missing_ok=True)
        return dated[-1][1]

    def _write_atomic(self, path: Path, data: bytes) -> None:
        tmp = self.tmp / f"{path.name}.{secrets.token_hex(4)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(data)
            while view:  # os.write may write less than asked (a full disk): never replace with a short file
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError("short write")
                view = view[n:]
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            tmp.unlink(missing_ok=True)
            raise
        os.close(fd)
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    @staticmethod
    def _aad(h: dict) -> bytes:
        keys = ("format", "id", "ref", "state", "created", "updated", "wrapped_key", "nonce_key")
        return json.dumps({k: h[k] for k in keys}, sort_keys=True, separators=(",", ":")).encode()

    # ------------------------------------------------------------------ API

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def refs_in_use(self) -> set[str]:
        if not self.dir.is_dir():
            return set()
        return {p.name[:4] for p in self.dir.glob("*.ldv") if _NAME.match(p.name)}

    def save(self, sid: str, ref: str, state: str, meta: dict, body: dict, created: float | None = None) -> None:
        if state not in STATES or not re.fullmatch(r"[A-Z0-9]{4}", ref) or not re.fullmatch(r"[0-9a-f]{32}", sid):
            raise VaultError("bad-session-fields")
        self._dirs()
        now = self.clock()
        if created is None:
            try:
                created = self._header(self._find(sid))["created"]
            except VaultError:
                created = now
        key = AESGCM.generate_key(256)  # a fresh data key on every save
        nonce_key = secrets.token_bytes(12)
        h = {"format": FORMAT, "id": sid, "ref": ref, "state": state, "created": created, "updated": now,
             "nonce_key": _b64(nonce_key)}
        h["wrapped_key"] = _b64(AESGCM(self._mk()).encrypt(nonce_key, key, sid.encode()))
        aad = self._aad(h)
        nm, nb = secrets.token_bytes(12), secrets.token_bytes(12)
        h["nonce_meta"], h["nonce_body"] = _b64(nm), _b64(nb)
        h["meta"] = _b64(AESGCM(key).encrypt(nm, json.dumps(meta, ensure_ascii=False).encode(), aad))
        h["body"] = _b64(AESGCM(key).encrypt(nb, json.dumps(body, ensure_ascii=False).encode(), aad))
        path = self._path(sid, ref)
        old = [p for p in self.dir.glob(f"*-{sid}.ldv") if p != path]
        self._write_atomic(path, json.dumps(h).encode())
        for p in old:
            p.unlink(missing_ok=True)

    def _header(self, path: Path) -> dict:
        try:
            h = json.loads(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise VaultError("unreadable-session") from exc
        if not isinstance(h, dict) or h.get("format") != FORMAT:
            raise VaultError("unknown-format")
        return h

    def _open(self, h: dict, part: str) -> dict:
        from cryptography.exceptions import InvalidTag

        try:
            key = AESGCM(self._mk()).decrypt(_unb64(h["nonce_key"]), _unb64(h["wrapped_key"]), h["id"].encode())
            data = AESGCM(key).decrypt(_unb64(h[f"nonce_{part}"]), _unb64(h[part]), self._aad(h))
        except (InvalidTag, KeyError, ValueError) as exc:
            raise VaultError("session-damaged") from exc
        return json.loads(data)

    def load(self, sid: str) -> tuple[SessionHeader, dict]:
        h = self._header(self._find(sid))
        return self._headerobj(h, self._open(h, "meta")), self._open(h, "body")

    def _headerobj(self, h: dict, meta: dict) -> SessionHeader:
        left = EXPIRE_DAYS - (self.clock() - h["created"]) / DAY
        return SessionHeader(h["id"], h["ref"], h["state"], h["created"], h["updated"], meta, left)

    def list(self) -> list[SessionHeader]:
        """All readable sessions, newest first. A damaged file is skipped (and kept)."""
        out = []
        if not self.dir.is_dir():
            return out
        for p in self.dir.glob("*.ldv"):
            try:
                h = self._header(p)
                out.append(self._headerobj(h, self._open(h, "meta")))
            except VaultError:
                continue
        newest: dict[str, SessionHeader] = {}
        for s in out:  # a leftover file of a renamed session never shows twice
            if s.id not in newest or s.updated > newest[s.id].updated:
                newest[s.id] = s
        return sorted(newest.values(), key=lambda s: -s.updated)

    def delete(self, sid: str) -> None:
        for p in self.dir.glob(f"*-{sid}.ldv"):
            p.unlink(missing_ok=True)

    def expire(self) -> list[SessionHeader]:
        """Delete sessions older than 30 days unless waiting for a draft; return the
        sessions to warn about (from day 27, and waiting sessions past 30)."""
        warn = []
        for s in self.list():
            if s.days_left is not None and s.days_left <= 0 and s.state != "waiting":
                self.delete(s.id)
            elif s.days_left is not None and s.days_left <= EXPIRE_DAYS - WARN_DAYS:
                warn.append(s)
        for p in self.tmp.glob("*.tmp") if self.tmp.is_dir() else []:  # leftovers of an interrupted write
            p.unlink(missing_ok=True)
        return warn

    def delete_everything(self) -> None:
        """Files first, then the key: if the files cannot go, the key stays, so what
        remains is still readable and the failure is reported."""
        try:
            shutil.rmtree(self.root)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise VaultError("delete-failed") from exc
        self.keystore.delete()
        self._master = None

    # ------------------------------------------------------------------ app data

    def save_json(self, name: str, data: Any) -> None:
        """Small encrypted app files (caseload, settings): same keys, same format."""
        if not re.fullmatch(r"[a-z]+", name):
            raise VaultError("bad-name")
        self._dirs()
        nonce = secrets.token_bytes(12)
        ct = AESGCM(self._mk()).encrypt(nonce, json.dumps(data, ensure_ascii=False).encode(), name.encode())
        self._write_atomic(self.root / f"{name}.ldj",
                           json.dumps({"format": FORMAT, "nonce": _b64(nonce), "data": _b64(ct)}).encode())

    def load_json(self, name: str, default: Any = None) -> Any:
        from cryptography.exceptions import InvalidTag

        path = self.root / f"{name}.ldj"
        if not path.exists():
            return default
        try:
            h = json.loads(path.read_bytes())
            return json.loads(AESGCM(self._mk()).decrypt(_unb64(h["nonce"]), _unb64(h["data"]), name.encode()))
        except (InvalidTag, KeyError, ValueError) as exc:
            raise VaultError(f"{name}-damaged") from exc
