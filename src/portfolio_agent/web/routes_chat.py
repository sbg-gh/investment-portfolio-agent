"""POST /api/chat: staged server-sent events over the unchanged M4 read-only chat gate.

The model's reply must be complete JSON before anything is used, so there is no token streaming:
events are status (immediately), then evidence, explanation, proposals and done. Message text and
model output are untrusted data; every event payload is a JSON value (text is never markup).
"""
import re
from uuid import uuid4

from . import routes_common as rc
from .app import Route
from .wire import WireError

MAX_MESSAGE = 2000
_CONVERSATION = re.compile(r'[A-Za-z0-9._:-]{1,64}', re.ASCII)


def chat(request):
    body = request.json
    message = body.get('message')
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE:
        raise WireError('invalid_field')
    portfolio_id = body.get('portfolio_id')
    if portfolio_id is not None and not isinstance(portfolio_id, str):
        raise WireError('invalid_field')
    conversation_id = body.get('conversation_id', uuid4().hex)
    if not isinstance(conversation_id, str) or not _CONVERSATION.fullmatch(conversation_id):
        raise WireError('invalid_field')
    context = request.context
    if portfolio_id is not None:
        # Own-portfolio check before any stream starts, so a foreign id is the generic 404.
        owned = request.run(lambda b: {p['id'] for p in b.service.portfolios(context)})
        if portfolio_id not in owned:
            raise PermissionError('Portfolio unavailable')

    def produce():
        out = request.run(lambda b: b.chat.chat(context, conversation_id=conversation_id,
                                                 message=message, portfolio_id=portfolio_id))
        return [('evidence', out['evidence']),
                ('explanation', {'text': out['explanation']}),
                ('proposals', out['proposed_actions']),
                ('done', {'model_status': out.get('model_status', 'ok'),
                          'conversation_id': out['conversation_id'],
                          'request_id': out['request_id']})]
    return produce


def chat_routes():
    return [Route('POST', '/api/chat', chat, private=True, unsafe=True, stream=True)]
