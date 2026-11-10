"""READ-ONLY specialist tools behind a deterministic schema gate.

The model only PROPOSES a (name, args) tool call as untrusted text. validate_proposal is a
deterministic allow-list + per-tool schema check: an unknown/aliased name, a missing/extra
arg, or a wrong-typed arg is refused BEFORE anything executes. Tools never accept an owner
id; the AuthContext is passed through at execution, so identity never comes from the model.
Security never depends on the model honoring a format.
"""
from dataclasses import dataclass

# Fixed provenance sources for the two valuation tools. The model can never choose a source.
INTRADAY_SOURCE = 'synthetic-intraday-quote'
NAV_SOURCE = 'synthetic-dated-nav'
FIXED_SOURCE_TOOLS = {'intraday_quote': INTRADAY_SOURCE, 'dated_nav': NAV_SOURCE}


class ProposalRejected(Exception):
    """A proposed tool call failed the deterministic schema/allow-list gate."""

    def __init__(self, reason, *, name=None):
        super().__init__(reason)
        self.reason = reason
        self.name = name


@dataclass(frozen=True)
class ToolSchema:
    name: str
    required: dict  # arg name -> expected python type

    def validate(self, args):
        if not isinstance(args, dict):
            raise ProposalRejected('Tool arguments must be an object', name=self.name)
        missing = [k for k in self.required if k not in args]
        if missing:
            raise ProposalRejected(f'Missing arguments: {sorted(missing)}', name=self.name)
        extra = [k for k in args if k not in self.required]
        if extra:
            raise ProposalRejected(f'Unexpected arguments: {sorted(extra)}', name=self.name)
        for key, expected in self.required.items():
            if not isinstance(args[key], expected):
                raise ProposalRejected(
                    f'Argument {key!r} must be {expected.__name__}', name=self.name)
        return dict(args)


@dataclass(frozen=True)
class ToolProposal:
    name: str
    args: dict


class ToolRegistry:
    """Fixed allow-list of READ-ONLY tools wrapping authorized PortfolioService reads."""

    def __init__(self):
        self._schemas = {
            'list_holdings': ToolSchema('list_holdings', {'portfolio_id': str}),
            'value_portfolio': ToolSchema('value_portfolio',
                                          {'portfolio_id': str, 'as_of': str, 'source': str}),
            'intraday_quote': ToolSchema('intraday_quote', {'portfolio_id': str, 'as_of': str}),
            'dated_nav': ToolSchema('dated_nav', {'portfolio_id': str, 'as_of': str}),
        }

    def allowed_names(self):
        return tuple(self._schemas)

    def schema(self, name):
        return self._schemas[name]

    def execute(self, name, args, service, context):
        """Run a validated read-only tool. Identity comes from the AuthContext only."""
        if name == 'list_holdings':
            return service.holdings(context, args['portfolio_id'])
        if name == 'value_portfolio' or name in FIXED_SOURCE_TOOLS:
            from datetime import datetime
            try:
                as_of = datetime.fromisoformat(args['as_of'])
            except (TypeError, ValueError) as exc:
                raise ProposalRejected('as_of must be an ISO-8601 timestamp', name=name) from exc
            # intraday_quote / dated_nav use a fixed source; only value_portfolio takes one.
            source = FIXED_SOURCE_TOOLS.get(name) or args['source']
            return service.value_portfolio(
                context, args['portfolio_id'], as_of=as_of, source=source)
        raise ProposalRejected('Tool is not executable', name=name)


def validate_proposal(name, args, allowed_tools):
    """Deterministic gate: refuse anything not matching a declared read-only tool exactly."""
    if not isinstance(name, str) or name not in allowed_tools:
        raise ProposalRejected('Unknown or disallowed tool', name=name if isinstance(name, str) else None)
    schema = ToolRegistry().schema(name)
    validated = schema.validate(args)
    return ToolProposal(name, validated)
