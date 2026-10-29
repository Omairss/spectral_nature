from __future__ import annotations

from typing import Callable, Iterator, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from compute.paper_sizing import RiskPolicy
from services import alpaca_paper, api_auth, trading_portfolio
from services.auth_service import UserContext


class SizingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_volatility_pct: float = Field(
        default=RiskPolicy.position_volatility_pct, gt=0, le=100
    )
    portfolio_volatility_pct: float = Field(
        default=RiskPolicy.portfolio_volatility_pct, gt=0, le=100
    )
    max_position_pct: float = Field(default=RiskPolicy.max_position_pct, gt=0, le=100)
    max_gross_pct: float = Field(default=RiskPolicy.max_gross_pct, gt=0, le=100)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(default="", max_length=160)
    policy: SizingPolicy | None = None
    intent: Literal["entry", "reduce", "close", "cancel"] = "entry"
    call_id: str = Field(default="", max_length=160)
    quantity: str | None = Field(default=None, pattern=r"^\d{1,12}(?:\.\d{1,6})?$")
    order_id: str = Field(default="", max_length=160)
    symbol: str = Field(default="", max_length=32)


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["approval", "autonomous"]
    entries_paused: bool
    risk_policy: SizingPolicy
    expected_revision: int = Field(ge=0)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: UUID
    arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_paper_trading_router(
    require_principal: Callable[..., api_auth.AuthPrincipal],
) -> APIRouter:
    router = APIRouter(prefix="/v1/trading-agent/paper")

    def owner(
        principal: api_auth.AuthPrincipal = Depends(require_principal),
    ) -> Iterator[UserContext]:
        context = principal.user_context
        if principal.principal_type != "user" or context is None:
            raise HTTPException(403, "A signed-in user is required.")
        try:
            alpaca_paper._admin(context)
            yield context
        except HTTPException:
            raise
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except alpaca_paper.PaperAccountError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                503, "Paper trading is temporarily unavailable."
            ) from exc

    @router.post("/refresh")
    def refresh(response: Response, context: UserContext = Depends(owner)) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        return alpaca_paper.refresh(context)

    @router.post("/review")
    def review(
        body: ReviewRequest, response: Response, context: UserContext = Depends(owner)
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        return alpaca_paper.review_order(
            context,
            **{
                **body.model_dump(exclude={"policy"}),
                "policy": (
                    RiskPolicy(**body.policy.model_dump()) if body.policy else None
                ),
            },
        )

    @router.post("/confirm")
    def confirm(
        body: ConfirmRequest, response: Response, context: UserContext = Depends(owner)
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        return alpaca_paper.confirm_order(
            context, ticket_id=str(body.ticket_id), arguments_hash=body.arguments_hash
        )

    @router.post("/policy")
    def policy(
        body: PolicyRequest, response: Response, context: UserContext = Depends(owner)
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        return trading_portfolio.change_policy(context, **body.model_dump())

    @router.get("/calls/{call_id}")
    def call(
        call_id: str, response: Response, context: UserContext = Depends(owner)
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        result = trading_portfolio.call_detail(context, call_id)
        if result is None:
            raise HTTPException(404, "Call not found.")
        return result

    return router
