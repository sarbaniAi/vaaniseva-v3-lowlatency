"""LiveKit real-time voice API — room management, token generation, agent dispatch.

Endpoints:
  POST /api/livekit/join   — Create room, start agent, return user token
  POST /api/livekit/leave  — Stop agent and clean up room
  GET  /api/livekit/status — Check if LiveKit is configured
"""

import logging
import uuid

from fastapi import APIRouter, Request

from vaaniseva.retrieval.genie import get_customer_profile, get_customer_loans
from vaaniseva.voice.livekit_agent import (
    LIVEKIT_URL,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    create_room,
    generate_token,
    start_voice_agent,
    get_active_agent,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/livekit", tags=["livekit-voice"])


@router.get("/status")
async def livekit_status():
    """Check if LiveKit is configured."""
    configured = all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET])
    return {
        "configured": configured,
        "url": LIVEKIT_URL if configured else None,
    }


@router.post("/join")
async def join_voice_call(request: Request):
    """Create a LiveKit room, start the voice agent, return a user token.

    Request body:
      { "customer_id": int, "call_purpose": "LOAN_RECOVERY" }

    Response:
      { "token": "...", "url": "wss://...", "room_name": "...",
        "call_id": "...", "customer_name": "..." }
    """
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        return {"error": "LiveKit not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET."}

    body = await request.json()
    customer_id = body.get("customer_id")
    call_purpose = body.get("call_purpose", "LOAN_RECOVERY")

    if not customer_id:
        return {"error": "customer_id required"}

    # Load customer data from Lakebase
    customer = get_customer_profile(customer_id)
    if not customer:
        return {"error": "Customer not found"}
    loans = get_customer_loans(customer_id)

    # Create LiveKit room
    call_id = str(uuid.uuid4())[:8]
    room_name = f"vaaniseva-{call_id}"
    try:
        await create_room(room_name)
    except Exception as e:
        logger.error(f"Failed to create LiveKit room: {e}")
        return {"error": f"LiveKit room creation failed: {e}"}

    # Generate user token (for browser)
    user_identity = f"customer-{customer_id}"
    user_token = generate_token(room_name, user_identity)

    # Start the voice agent (livekit-agents framework + Sarvam STT/TTS)
    try:
        await start_voice_agent(room_name, customer, loans, call_purpose)
    except Exception as e:
        logger.error(f"Failed to start voice agent: {e}")
        return {"error": f"Voice agent start failed: {e}"}

    logger.info(
        f"LiveKit voice call started: room={room_name}, "
        f"call_id={call_id}, customer={customer.get('name')}, "
        f"loans={len(loans)}"
    )

    return {
        "token": user_token,
        "url": LIVEKIT_URL,
        "room_name": room_name,
        "call_id": call_id,
        "customer_name": customer.get("name"),
        "stage": "GREETING",
    }


@router.post("/dial")
async def dial_phone_call(request: Request):
    """Start agent + dial an outbound phone call via SIP.

    Request body:
      { "customer_id": int, "to_number": "+91...", "call_purpose": "LOAN_RECOVERY" }
    """
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        return {"error": "LiveKit not configured."}

    body = await request.json()
    customer_id = body.get("customer_id")
    to_number = body.get("to_number")
    call_purpose = body.get("call_purpose", "LOAN_RECOVERY")

    if not customer_id or not to_number:
        return {"error": "customer_id and to_number required"}

    # Load customer data from Lakebase
    customer = get_customer_profile(customer_id)
    if not customer:
        return {"error": "Customer not found"}
    loans = get_customer_loans(customer_id)

    # Step 1: Create room + connect agent to room (but don't start AI yet)
    call_id = str(uuid.uuid4())[:8]
    room_name = f"vaaniseva-{call_id}"
    try:
        await create_room(room_name)
    except Exception as e:
        return {"error": f"Room creation failed: {e}"}

    from vaaniseva.voice.livekit_agent import VaaniSevaVoiceSession, _active_agents
    session = VaaniSevaVoiceSession(room_name, customer, loans, call_purpose)
    _active_agents[room_name] = session

    try:
        await session.connect_room()
    except Exception as e:
        return {"error": f"Room connect failed: {e}"}

    # Step 2: Dial the phone — blocks until answered
    dial_result = await session.dial_phone(to_number)

    if dial_result.get("status") != "answered":
        logger.warning(f"Call not answered: {dial_result}")
        return {
            "call_id": call_id,
            "room_name": room_name,
            "to_number": to_number,
            "customer_name": customer.get("name"),
            "dial_status": dial_result.get("status"),
            "error": dial_result.get("error"),
        }

    # Step 3: Call answered — NOW start the AI agent (greeting plays to live caller)
    try:
        await session.start_agent()
    except Exception as e:
        logger.error(f"Agent start failed after answer: {e}")
        return {"error": f"Agent start failed: {e}"}

    logger.info(
        f"Phone call active: room={room_name}, to={to_number}, "
        f"customer={customer.get('name')}"
    )

    return {
        "call_id": call_id,
        "room_name": room_name,
        "to_number": to_number,
        "customer_name": customer.get("name"),
        "dial_status": "answered",
    }


@router.post("/leave")
async def leave_voice_call(request: Request):
    """Stop the voice agent and clean up."""
    body = await request.json()
    room_name = body.get("room_name", "")

    agent = get_active_agent(room_name)
    if agent:
        await agent.stop()

    return {"status": "left", "room_name": room_name}
