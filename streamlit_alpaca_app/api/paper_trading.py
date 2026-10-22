from __future__ import annotations

from typing import Callable, Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from compute.paper_sizing import RiskPolicy
from services import alpaca_paper, api_auth
from services.auth_service import UserContext


class SizingPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    position_volatility_pct: float = Field(default=RiskPolicy.position_volatility_pct, gt=0, le=100)
    portfolio_volatility_pct: float = Field(default=RiskPolicy.portfolio_volatility_pct, gt=0, le=100)
    max_position_pct: float = Field(default=RiskPolicy.max_position_pct, gt=0, le=100)
    max_gross_pct: float = Field(default=RiskPolicy.max_gross_pct, gt=0, le=100)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: str = Field(min_length=1, max_length=160)
    policy: SizingPolicy = Field(default_factory=SizingPolicy)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticket_id: UUID
    arguments_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def create_paper_trading_router(require_principal: Callable[..., api_auth.AuthPrincipal]) -> APIRouter:
    router = APIRouter(prefix='/v1/trading-agent/paper')

    def owner(principal: api_auth.AuthPrincipal = Depends(require_principal)) -> Iterator[UserContext]:
        context = principal.user_context
        if principal.principal_type != 'user' or context is None:
            raise HTTPException(403, 'A signed-in user is required.')
        try:
            alpaca_paper._admin(context)
            yield context
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except alpaca_paper.PaperAccountError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, 'Paper trading is temporarily unavailable.') from exc

    @router.post('/refresh')
    def refresh(response: Response, context: UserContext = Depends(owner)) -> dict:
        response.headers['Cache-Control'] = 'private, no-store'
        return alpaca_paper.refresh(context)

    @router.post('/review')
    def review(body: ReviewRequest, response: Response, context: UserContext = Depends(owner)) -> dict:
        response.headers['Cache-Control'] = 'private, no-store'
        return alpaca_paper.review_order(context, candidate_id=body.candidate_id, policy=RiskPolicy(**body.policy.model_dump()))

    @router.post('/confirm')
    def confirm(body: ConfirmRequest, response: Response, context: UserContext = Depends(owner)) -> dict:
        response.headers['Cache-Control'] = 'private, no-store'
        return alpaca_paper.confirm_order(context, ticket_id=str(body.ticket_id), arguments_hash=body.arguments_hash)

    return router
