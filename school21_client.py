"""Минимальный клиент School21 API. Только requests, без сторонних SDK."""

from __future__ import annotations

import time

import requests

AUTH_URL = "https://auth.21-school.ru/auth/realms/EduPowerKeycloak/protocol/openid-connect/token"
API_BASE = "https://platform.21-school.ru/services/21-school/api/v1"
STORAGE_BASE = "https://platform.21-school.ru/services/storage/download"
CLIENT_ID = "s21-open-api"


class School21Error(RuntimeError):
    pass


class School21Client:
    def __init__(self, login: str, password: str):
        self._login = login
        self._password = password
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def _authenticate(self) -> None:
        response = requests.post(
            AUTH_URL,
            data={
                "username": self._login,
                "password": self._password,
                "grant_type": "password",
                "client_id": CLIENT_ID,
            },
            timeout=30,
        )
        if not response.ok:
            raise School21Error(f"Auth failed [{response.status_code}]: {response.text}")

        data = response.json()
        self._access_token = data["access_token"]
        self._expires_at = time.monotonic() + data["expires_in"] - 30  # запас в 30с

    def _ensure_token(self) -> str:
        if self._access_token is None or time.monotonic() >= self._expires_at:
            self._authenticate()
        return self._access_token

    def _do_get(self, path: str, params: dict | None, token: str, timeout: int) -> requests.Response:
        return requests.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=timeout,
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        token = self._ensure_token()
        try:
            response = self._do_get(path, params, token, timeout=40)
        except requests.Timeout:
            # платформа иногда тормозит — одна повторная попытка
            response = self._do_get(path, params, token, timeout=60)

        if not response.ok:
            raise School21Error(f"GET {path} failed [{response.status_code}]: {response.text}")
        if not response.text.strip():
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text[:2000]}

    def get(self, path: str, params: dict | None = None) -> dict:
        """Универсальный GET к любому эндпоинту /v1/..."""
        return self._get(path, params)

    def download(self, storage_path: str) -> bytes:
        """Скачивает файл из хранилища платформы (например, iconUrl бейджа).

        Пути вида /public_any/... требуют Bearer-токен, поэтому просто
        отдать ссылку Telegram нельзя — качаем сами.
        """
        token = self._ensure_token()
        response = requests.get(
            f"{STORAGE_BASE}{storage_path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=40,
        )
        if not response.ok:
            raise School21Error(f"GET storage {storage_path} failed [{response.status_code}]")
        return response.content

    def participant(self, login: str) -> dict:
        return self._get(f"/participants/{login}")

    def participant_projects(self, login: str, status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        params = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        return self._get(f"/participants/{login}/projects", params=params)
