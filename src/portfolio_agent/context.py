"""Bounded, owner-scoped context assembly for the chat model.

Authorizes the portfolio through the M1 PortfolioService path BEFORE any read, gathers ONLY
that owner's holdings plus an M2a valuation (reusing valuation.value_portfolio's fresh|stale|
missing status and as-of times), serializes to a deterministic prompt string, and enforces
estimate_tokens(prompt) <= budget. It never fabricates values and never silently exceeds the
budget: an over-budget assembly raises BoundedContextError. All reads flow through the owner's
AuthContext, so one user's data can never enter another user's context.
"""
from dataclasses import dataclass

from .model import estimate_tokens


class BoundedContextError(Exception):
    """Assembled context would exceed the configured token budget."""


@dataclass(frozen=True)
class AssembledContext:
    prompt: str
    holdings: list
    valuation: dict
    token_estimate: int


def _render(portfolio_id, holdings, valuation):
    lines = [f'portfolio: {portfolio_id}', 'holdings:']
    for security_id, quantity in holdings:
        lines.append(f'  - {security_id} qty={quantity}')
    lines.append(f"valuation_status: {valuation['status']}")
    lines.append(f"market_as_of: {valuation['market_as_of']}")
    lines.append('evidence:')
    for line in valuation['lines']:
        lines.append(
            f"  - {line['security_id']} status={line['status']} "
            f"market_value={line['market_value']} as_of={line['observed_at']}")
    return '\n'.join(lines)


def assemble_context(service, context, portfolio_id, *, as_of, source, budget, policy=None):
    """Authorize, then gather owner-scoped holdings + valuation into a bounded prompt."""
    # Authorization happens inside each service call (M1 Store._authorize under _run);
    # an unauthorized portfolio_id raises PermissionError before any private read returns.
    holdings = service.holdings(context, portfolio_id)
    valuation = service.value_portfolio(
        context, portfolio_id, as_of=as_of, source=source, policy=policy)
    prompt = _render(portfolio_id, holdings, valuation)
    estimate = estimate_tokens(prompt)
    if estimate > budget:
        raise BoundedContextError(
            f'Assembled context {estimate} tokens exceeds budget {budget}')
    return AssembledContext(prompt=prompt, holdings=holdings, valuation=valuation,
                            token_estimate=estimate)
