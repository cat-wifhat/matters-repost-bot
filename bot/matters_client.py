"""Minimal Matters GraphQL client for repost-bot.

Implements only what we need: emailLogin, singleFileUpload (multipart),
putDraft, and publishArticle.
"""
import json
import logging
import mimetypes
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from .config import MATTERS_API, USER_AGENT

log = logging.getLogger(__name__)


class MattersError(RuntimeError):
    pass


class MattersClient:
    def __init__(self, api_url: str = MATTERS_API):
        self.api_url = api_url
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "x-client-name": "p-articles-repost-bot",
        })
        self.token: Optional[str] = None

    def _gql(self, query: str, variables: Optional[dict] = None) -> dict:
        headers = {}
        if self.token:
            headers["x-access-token"] = self.token
        payload = {"query": query, "variables": variables or {}}
        resp = self.session.post(self.api_url, json=payload, headers=headers, timeout=60)
        try:
            body = resp.json()
        except ValueError:
            raise MattersError(f"Non-JSON response (status {resp.status_code}): {resp.text[:300]}")
        if "errors" in body and body["errors"]:
            raise MattersError(f"GraphQL error: {body['errors']}")
        if "data" not in body:
            raise MattersError(f"No data in response: {body}")
        return body["data"]

    # ---- auth ----

    def login(self, email: str, password: str) -> str:
        query = """
        mutation Login($input: EmailLoginInput!) {
          emailLogin(input: $input) { auth token type }
        }
        """
        data = self._gql(query, {"input": {"email": email, "passwordOrCode": password}})
        result = data["emailLogin"]
        if not result.get("auth") or not result.get("token"):
            raise MattersError(f"Login failed: {result}")
        self.token = result["token"]
        log.info("Logged in to Matters (type=%s)", result.get("type"))
        return self.token

    # ---- drafts ----

    def create_empty_draft(self, title: str) -> str:
        """Create a placeholder draft to get an ID we can attach uploads to."""
        query = """
        mutation NewDraft($input: PutDraftInput!) {
          putDraft(input: $input) { id }
        }
        """
        data = self._gql(query, {"input": {"title": title}})
        return data["putDraft"]["id"]

    def update_draft(
        self,
        draft_id: str,
        *,
        title: str,
        content: str,
        summary: Optional[str] = None,
        tags: Optional[list[str]] = None,
        cover_asset_id: Optional[str] = None,
        license: str = "arr",
    ) -> dict:
        query = """
        mutation UpdateDraft($input: PutDraftInput!) {
          putDraft(input: $input) {
            id
            title
            slug
            publishState
          }
        }
        """
        inp: dict[str, Any] = {
            "id": draft_id,
            "title": title,
            "content": content,
            "license": license,
        }
        if summary is not None:
            inp["summary"] = summary
        if tags:
            inp["tags"] = tags
        if cover_asset_id:
            inp["cover"] = cover_asset_id
        return self._gql(query, {"input": inp})["putDraft"]

    def publish_draft(self, draft_id: str) -> dict:
        query = """
        mutation Publish($input: PublishArticleInput!) {
          publishArticle(input: $input) { id publishState }
        }
        """
        return self._gql(query, {"input": {"id": draft_id}})["publishArticle"]

    # ---- assets ----

    def upload_image_file(
        self,
        content: bytes,
        filename: str,
        mime: str,
        draft_id: str,
        *,
        asset_type: str = "embed",
    ) -> dict:
        """Upload image bytes to Matters via the GraphQL multipart spec.

        We use this instead of directImageUpload-by-URL because Cloudflare on
        p-articles blocks Matters' server-side image fetcher, leaving 404 assets.

        Returns the Asset dict {id, path, type}.
        """
        query = """
        mutation Upload($input: SingleFileUploadInput!) {
          singleFileUpload(input: $input) { id path type }
        }
        """
        # Note: SingleFileUploadInput has no `mime` field — Matters derives it
        # from the multipart part's Content-Type. We still pass `mime` into the
        # multipart part below.
        operations = json.dumps({
            "query": query,
            "variables": {
                "input": {
                    "type": asset_type,
                    "file": None,
                    "entityType": "draft",
                    "entityId": draft_id,
                }
            },
        })
        map_data = json.dumps({"0": ["variables.input.file"]})
        # `files` triggers multipart in requests; do NOT include the json
        # Content-Type header (requests will set the right boundary header).
        files = {
            "operations": (None, operations, "application/json"),
            "map": (None, map_data, "application/json"),
            "0": (filename, content, mime),
        }
        headers = {
            "User-Agent": USER_AGENT,
            "x-client-name": "p-articles-repost-bot",
            # Apollo Server's CSRF protection rejects multipart requests unless
            # one of these "preflight" headers is present.
            "apollo-require-preflight": "true",
            "x-apollo-operation-name": "Upload",
        }
        if self.token:
            headers["x-access-token"] = self.token
        resp = requests.post(self.api_url, files=files, headers=headers, timeout=120)
        try:
            body = resp.json()
        except ValueError:
            raise MattersError(f"Non-JSON upload response (status {resp.status_code}): {resp.text[:300]}")
        if body.get("errors"):
            raise MattersError(f"Upload GraphQL error: {body['errors']}")
        return body["data"]["singleFileUpload"]
