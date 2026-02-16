from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.models import ChatMessageRequest, ChatResponse, ConversationMessage
from app.services.chat_service import chat_service

router = APIRouter(tags=["chat"])


@router.post("/wikis/{wiki_id}/chat")
async def chat(wiki_id: str, request: ChatMessageRequest):
    """Send a message and get a streamed response via SSE."""

    async def event_generator():
        async for event in chat_service.handle_message(
            wiki_id=wiki_id,
            message=request.message,
            conversation_id=request.conversation_id,
        ):
            yield {"event": event["type"], "data": event.get("data", {})}

    return EventSourceResponse(event_generator())


@router.get("/wikis/{wiki_id}/chat/{conversation_id}")
async def get_conversation(
    wiki_id: str, conversation_id: str
) -> list[ConversationMessage]:
    messages = await chat_service.get_conversation(wiki_id, conversation_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return messages
