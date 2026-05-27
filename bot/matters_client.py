"""Minimal Matters GraphQL client for repost-bot.

Implements only what we need: emailLogin, directImageUpload (by URL),
putDraft, and publishArticle.
"""
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

    def upload_image_by_url(
        self,
        url: str,
        draft_id: str,
        *,
        asset_type: str = "embed",
        mime: Optional[str] = None,
    ) -> dict:
        """Tell Matters to fetch and store an image from `url`. Returns {id, path}.

        `asset_type` is the Matters AssetType — use "embed" for body images,
        "cover" for the article cover.
        """
        query = """
        mutation DirectUpload($input: DirectImageUploadInput!) {
          directImageUpload(input: $input) { id path type }
        }
        """
        # Matters' API errors out with "mime needs to be specified" if we omit it.
        # Guess from the URL extension; fall back to image/png since p-articles
        # serves mostly PNGs.
        if not mime:
            guessed, _ = mimetypes.guess_type(urlparse(url).path)
            mime = guessed or "image/png"
        inp: dict[str, Any] = {
            "type": asset_type,
            "url": url,
            "mime": mime,
            "entityType": "draft",
            "entityId": draft_id,
        }
        return self._gql(query, {"input": inp})["directImageUpload"]
