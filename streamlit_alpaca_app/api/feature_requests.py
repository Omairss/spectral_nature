from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterator, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from services import api_auth
from services.feature_requests import (
    FeatureRequestConflict, FeatureRequestNotFound, FeatureRequestRepository,
    FeatureRequestUnavailable,
)


class FeatureRequestCreate(BaseModel):
    id: UUID
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    client: str = Field(default="", max_length=40)
    app_version: str = Field(default="", max_length=80)
    context: str = Field(default="", max_length=400)


class FeatureRequestMessageCreate(BaseModel):
    id: UUID
    body: str = Field(min_length=1, max_length=8000)


class FeatureRequestMessageResponse(BaseModel):
    id: str
    author_id: str
    author_name: str
    author_role: str
    body: str
    created_at: datetime


class FeatureRequestSummaryResponse(BaseModel):
    id: str
    owner_id: str
    owner_name: str
    title: str
    client: str
    app_version: str
    context: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class FeatureRequestDetailResponse(FeatureRequestSummaryResponse):
    messages: list[FeatureRequestMessageResponse]


class FeatureRequestListResponse(BaseModel):
    can_review_all: bool
    requests: list[FeatureRequestSummaryResponse]


def create_feature_request_router(require_principal: Callable[..., api_auth.AuthPrincipal]) -> APIRouter:
    router = APIRouter(prefix="/v1/feature-requests")

    def _feature_request_repository(
        principal: api_auth.AuthPrincipal = Depends(require_principal),
    ) -> Iterator[FeatureRequestRepository]:
        context = principal.user_context
        if principal.principal_type != "user" or context is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "User session required.")
        try:
            with FeatureRequestRepository.open(context) as repository:
                yield repository
        except FeatureRequestNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature request not found.")
        except FeatureRequestConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        except PermissionError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required.")
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
        except FeatureRequestUnavailable:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Feature requests are unavailable.")


    @router.get("", response_model=FeatureRequestListResponse)
    def list_feature_requests(
        response: Response,
        scope: Literal["mine", "all"] = "mine",
        repository: FeatureRequestRepository = Depends(_feature_request_repository),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return repository.list_requests(all_requests=scope == "all")


    @router.post("", response_model=FeatureRequestDetailResponse)
    def create_feature_request(
        body: FeatureRequestCreate,
        response: Response,
        repository: FeatureRequestRepository = Depends(_feature_request_repository),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return repository.create_request(request_id=str(body.id), title=body.title, body=body.body,
                                         client=body.client, app_version=body.app_version, context=body.context)


    @router.get("/{request_id}", response_model=FeatureRequestDetailResponse)
    def get_feature_request(
        request_id: UUID,
        response: Response,
        repository: FeatureRequestRepository = Depends(_feature_request_repository),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return repository.get_request(str(request_id))


    @router.post("/{request_id}/messages", response_model=FeatureRequestDetailResponse)
    def add_feature_request_message(
        request_id: UUID,
        body: FeatureRequestMessageCreate,
        response: Response,
        repository: FeatureRequestRepository = Depends(_feature_request_repository),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return repository.add_message(str(request_id), message_id=str(body.id), body=body.body)

    return router
