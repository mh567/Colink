"""Native device enrollment; management roles never grant device possession."""

from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from octop.infra.db.repos.devices import DeviceRow as DeviceRow
from octop.infra.db.repos.devices import DeviceStatus as DeviceStatus
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User

DEVICE_TIMEOUT_SECONDS = 15


@dataclass
class DeviceConnection:
    device_id: str
    user_id: int
    password_version: int
    mfa_version: int
    seen_at: float


class DeviceManager:
    def __init__(self, services: SharedServices) -> None:
        self.services = services
        self._connections: dict[str, DeviceConnection] = {}

    @staticmethod
    def verify_proof(public_key: str, message: str, signature: str) -> None:
        try:
            raw_key = base64.b64decode(public_key, validate=True)
            if base64.b64encode(raw_key).decode() != public_key:
                raise ValueError("noncanonical device key")
            key = Ed25519PublicKey.from_public_bytes(raw_key)
            key.verify(base64.b64decode(signature, validate=True), message.encode())
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise OctopError(ErrorCode.AUTH_FAILED, "invalid device proof") from exc

    def _active_user(self, user_id: int) -> tuple[int, int]:
        row = self.services.user_repo.get(user_id)
        if row is None or row.disabled:
            raise OctopError(ErrorCode.USER_DISABLED, "device owner is not active")
        return int(row.password_version), int(row.mfa_version)

    def connect(self, user: User, *, public_key: str, name: str, platform: str) -> DeviceConnection:
        versions = self._active_user(user.id)
        row = self.services.device_repo.enroll(
            user_id=user.id, public_key=public_key, name=name, platform=platform
        )
        if row.owner_user_id != user.id or row.status == "revoked":
            raise OctopError(ErrorCode.FORBIDDEN, "device is not allowed for this employee")
        previous = self._connections.get(row.device_id)
        if previous is not None and self._connection_valid(previous):
            raise OctopError(ErrorCode.FORBIDDEN, "device already has a live connection")
        connection = DeviceConnection(row.device_id, user.id, *versions, time.monotonic())
        self._connections[row.device_id] = connection
        self.services.audit_repo.write(
            actor=user.username, action="device.connected", target=row.device_id
        )
        self.services.device_repo.touch(row.device_id)
        return connection

    def _connection_valid(self, connection: DeviceConnection) -> bool:
        if time.monotonic() - connection.seen_at >= DEVICE_TIMEOUT_SECONDS:
            return False
        row = self.services.user_repo.get(connection.user_id)
        return bool(
            row is not None
            and not row.disabled
            and int(row.password_version) == connection.password_version
            and int(row.mfa_version) == connection.mfa_version
        )

    def heartbeat(self, connection: DeviceConnection) -> DeviceRow:
        row = self.services.device_repo.get(connection.device_id)
        if (
            self._connections.get(connection.device_id) is not connection
            or not self._connection_valid(connection)
            or row is None
            or row.owner_user_id != connection.user_id
            or row.status == "revoked"
        ):
            raise OctopError(ErrorCode.FORBIDDEN, "device connection is no longer authorized")
        connection.seen_at = time.monotonic()
        self.services.device_repo.touch(row.device_id)
        return row

    def is_online(self, row: DeviceRow) -> bool:
        connection = self._connections.get(row.device_id)
        return bool(
            row.status == "approved"
            and connection is not None
            and self._connection_valid(connection)
        )

    def disconnect(self, connection: DeviceConnection) -> None:
        if self._connections.get(connection.device_id) is connection:
            self._connections.pop(connection.device_id)

    def clear(self) -> None:
        self._connections.clear()

    def list(self, user: User, *, managed: bool = False) -> list[DeviceRow]:
        if managed and not user.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "only administrators can manage devices")
        return self.services.device_repo.list(None if managed else user.id)

    def set_status(self, actor: User, device_id: str, status: DeviceStatus) -> DeviceRow:
        if not actor.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "only administrators can manage devices")
        row = self.services.device_repo.get(device_id)
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "device not found")
        if status == "approved":
            self._active_user(row.owner_user_id)
        self.services.device_repo.set_status(device_id, status)
        if status == "revoked":
            self._connections.pop(device_id, None)
        self.services.audit_repo.write(
            actor=actor.username, action=f"device.{status}", target=device_id
        )
        updated = self.services.device_repo.get(device_id)
        assert updated is not None
        return updated
