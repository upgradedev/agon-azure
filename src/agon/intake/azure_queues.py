"""Azure Storage Queues behind `agon.intake.drain.QueuePort`, keyless (managed identity).

The Azure SDK is imported when the adapter is built, so the rest of `agon.intake` imports with the
standard library only. Messages are read and written as raw text (no SDK encode or decode
policy): the inbound queues carry base64 written by the Logic Apps and decoded by
`agon.intake.decode_message`; the `decisions` and `intake-poison` messages are plain JSON.
"""

from __future__ import annotations

from typing import Any

from agon.intake.drain import InboundMessage


class AzureQueuePort:
    def __init__(self, account_url: str, credential: Any) -> None:
        from azure.storage.queue import QueueServiceClient  # imported here on purpose

        self._service = QueueServiceClient(account_url=account_url, credential=credential)
        self._clients: dict[str, Any] = {}

    def _client(self, queue: str) -> Any:
        if queue not in self._clients:
            self._clients[queue] = self._service.get_queue_client(queue, message_encode_policy=None, message_decode_policy=None)
        return self._clients[queue]

    def receive(self, queue: str, max_messages: int, visibility_timeout: int) -> list[InboundMessage]:
        pages = self._client(queue).receive_messages(messages_per_page=max_messages, visibility_timeout=visibility_timeout, max_messages=max_messages)
        return [
            InboundMessage(message_id=m.id, pop_receipt=m.pop_receipt, text=m.content, dequeue_count=int(m.dequeue_count or 0))
            for m in pages
        ]

    def delete(self, queue: str, message: InboundMessage) -> None:
        self._client(queue).delete_message(message.message_id, message.pop_receipt)

    def send(self, queue: str, text: str) -> None:
        self._client(queue).send_message(text)


def managed_identity_credential(client_id: str | None) -> Any:
    """The user-assigned identity of the app (id-api), or the environment's default chain in CI."""
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)
