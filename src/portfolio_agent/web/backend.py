"""The objects a request handler may use. Built and used on the single Store-owning thread."""
from dataclasses import dataclass
from typing import Any


@dataclass
class Backend:
    store: Any
    auth: Any
    service: Any
    ops: Any
    chat: Any

    def close(self):
        self.store.close()
