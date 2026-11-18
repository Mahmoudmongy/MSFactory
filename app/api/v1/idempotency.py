"""Idempotency helper.

A write endpoint may declare ``Idempotency-Key``. If we have already processed
that key for the same endpoint we replay the stored response, so a client retry
(flaky mobile network, double tap) never creates a second document.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from ...repositories.documents import IdempotencyRepository


def replay_or_execute[T: BaseModel](
    db: Session,
    company_id: int,
    key: str | None,
    endpoint: str,
    handler: Callable[[], T],
    response_model: type[T],
) -> T:
    repo = IdempotencyRepository(db, company_id)

    if key:
        existing = repo.find(key)
        if existing:
            if existing.response_status >= 400:
                raise HTTPException(
                    status_code=existing.response_status,
                    detail=json.loads(existing.response_body or "{}"),
                )
            try:
                return response_model.model_validate(json.loads(existing.response_body))
            except (ValidationError, ValueError):
                # Stored payload no longer matches the schema; fall through and
                # let the handler produce a fresh, valid response.
                pass

    result = handler()
    if key:
        repo.store(
            key,
            endpoint,
            status.HTTP_201_CREATED,
            json.dumps(result.model_dump(mode="json"), default=str),
        )
        db.commit()
    return result
