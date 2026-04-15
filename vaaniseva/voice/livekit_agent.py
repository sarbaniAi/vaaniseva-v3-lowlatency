"""LiveKit voice agent — real-time voice pipeline for VaaniSeva.

Uses livekit-agents framework with Sarvam STT/TTS plugins (as recommended
by Sarvam AI cookbook). LiveKit works with Databricks Apps because both
browser and agent make OUTBOUND connections to LiveKit Cloud.

Architecture:
  Browser (mic) --WebRTC--> LiveKit Cloud <--WebSocket-- VaaniSeva Agent
                                                          |
                                               Sarvam STT saaras:v3 (streaming)
                                               Sarvam-M LLM (via OpenAI-compat API)
                                               Sarvam TTS bulbul:v3 (streaming)
                                                          |
  Browser (speaker) <--WebRTC-- LiveKit Cloud --WebSocket--> Agent publishes audio

Data: Customer profiles + loans from Databricks Lakebase.
Deployment: Databricks App (FastAPI + this agent).

Reference: https://docs.sarvam.ai/api-reference-docs/cookbook/example-voice-agents/collection-agent
"""

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterable

import aiohttp
from livekit import api as lk_api, rtc
from livekit.agents import llm
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import openai as openai_plugin, sarvam, silero

from vaaniseva.voice.sarvam_tts_streaming import SarvamStreamingTTS


logger = logging.getLogger(__name__)

# LiveKit config
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")

# Sarvam AI
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")

# SIP (outbound phone calls)
SIP_OUTBOUND_TRUNK_ID = os.environ.get("SIP_OUTBOUND_TRUNK_ID", "")

# Track active agents per room
_active_agents: dict[str, "VaaniSevaVoiceSession"] = {}


# ---------------------------------------------------------------------------
# Sarvam-M LLM wrapper — fixes message alternation requirement
# ---------------------------------------------------------------------------

class SarvamFixMiddleware:
    """httpx transport middleware that fixes Sarvam-M's strict message
    alternation requirement at the HTTP request level.

    Intercepts POST /chat/completions and ensures messages start with
    a user turn after system messages.
    """

    def __init__(self, transport):
        self._transport = transport

    def handle_request(self, request):
        import json as _json
        if request.method == b"POST" and b"/chat/completions" in request.url.raw_path:
            try:
                body = _json.loads(request.content)
                messages = body.get("messages", [])
                fixed = self._fix_messages(messages)
                body["messages"] = fixed
                new_content = _json.dumps(body).encode()
                request = request.copy_with(content=new_content)
                # Update content-length header
                request.headers["content-length"] = str(len(new_content))
            except Exception as e:
                logger.warning(f"SarvamFixMiddleware error: {e}")
        return self._transport.handle_request(request)

    def _fix_messages(self, messages):
        """Ensure first non-system message is from user."""
        fixed = []
        has_user = False
        for msg in messages:
            role = msg.get("role", "")
            if role == "system":
                fixed.append(msg)
            elif not has_user and role == "assistant":
                fixed.append({"role": "user", "content": "(Call connected. Greet the customer.)"})
                fixed.append(msg)
                has_user = True
            else:
                if role == "user":
                    has_user = True
                fixed.append(msg)

        if not has_user:
            # No user message at all — add one at the end
            fixed.append({"role": "user", "content": "(Call connected. Greet the customer.)"})
        return fixed

    def close(self):
        self._transport.close()


def _create_sarvam_tts(http_session):
    """Create Sarvam TTS with streaming disabled.

    The livekit-plugins-sarvam WebSocket TTS returns MP3 audio which the
    livekit-agents WAV decoder can't handle. Force REST (chunked) mode
    which returns proper WAV audio.
    """
    from livekit.agents import tts as tts_module

    tts_instance = sarvam.TTS(
        target_language_code="hi-IN",
        model="bulbul:v2",
        speaker="anushka",
        api_key=SARVAM_API_KEY,
        http_session=http_session,
    )
    # Override capabilities to disable streaming — forces REST path
    tts_instance._capabilities = tts_module.TTSCapabilities(streaming=False)
    return tts_instance


def _create_sarvam_llm():
    """Create an OpenAI-compatible LLM pointing to Sarvam with message fix."""
    import httpx
    from openai import OpenAI

    transport = SarvamFixMiddleware(httpx.HTTPTransport())
    http_client = httpx.Client(transport=transport)

    oai_client = OpenAI(
        base_url="https://api.sarvam.ai/v1",
        api_key=SARVAM_API_KEY,
        http_client=http_client,
    )

    return openai_plugin.LLM(
        model="sarvam-m",
        base_url="https://api.sarvam.ai/v1",
        api_key=SARVAM_API_KEY,
        client=oai_client,
    )


# ---------------------------------------------------------------------------
# Text transform: strip <think>...</think> blocks from Sarvam-M output
# ---------------------------------------------------------------------------

async def strip_think_blocks(text_stream: AsyncIterable[str]) -> AsyncIterable[str]:
    """Strip <think>...</think> reasoning blocks from streamed LLM output.

    Sarvam-M always emits <think>...</think> before the actual dialogue.
    This transform buffers until </think> is found, then yields only
    the spoken dialogue that follows.
    """
    buffer = ""
    inside_think = False
    think_done = False

    async for chunk in text_stream:
        if think_done:
            # Already past the think block — yield everything
            yield chunk
            continue

        buffer += chunk

        if not inside_think:
            # Check if <think> starts
            if "<think>" in buffer:
                inside_think = True
                # Drop everything from <think> onwards (still buffering)
                pre_think = buffer.split("<think>")[0]
                if pre_think.strip():
                    yield pre_think
                buffer = buffer.split("<think>", 1)[1]
            else:
                # No think block yet — yield what we have
                yield chunk

        if inside_think and not think_done:
            # Look for </think> closing tag
            if "</think>" in buffer:
                think_done = True
                after_think = buffer.split("</think>", 1)[1]
                buffer = ""
                if after_think.strip():
                    yield after_think


# ---------------------------------------------------------------------------
# Token & Room helpers
# ---------------------------------------------------------------------------

def generate_token(room_name: str, identity: str) -> str:
    """Generate a LiveKit JWT access token."""
    token = lk_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token = token.with_identity(identity).with_name(identity)
    grants = lk_api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
    )
    token = token.with_grants(grants)
    return token.to_jwt()


async def create_room(room_name: str) -> dict:
    """Create a LiveKit room via REST API."""
    lk = lk_api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        room = await lk.room.create_room(
            lk_api.CreateRoomRequest(name=room_name, empty_timeout=300)
        )
        return {"name": room.name, "sid": room.sid}
    finally:
        await lk.aclose()


# ---------------------------------------------------------------------------
# Build agent instructions from Lakebase data
# ---------------------------------------------------------------------------

def build_collection_instructions(customer: dict, loans: list[dict], call_purpose: str) -> str:
    """Build rich agent instructions with real customer data from Lakebase.

    Following the Sarvam cookbook pattern: all context goes into the
    instructions so the LLM naturally drives the conversation.
    """
    name = customer.get("name", "Customer")
    city = customer.get("city", "")
    phone = customer.get("phone", "")
    lang_pref = customer.get("language_pref", "hi")
    risk_tier = customer.get("risk_tier", "MEDIUM")

    # Format loan details
    loan_details = ""
    if loans:
        for i, loan in enumerate(loans, 1):
            loan_details += (
                f"\n  Loan {i}: {loan.get('loan_type', 'Personal Loan')}"
                f"\n    - EMI Amount: ₹{loan.get('emi_amount', 0):,.0f}"
                f"\n    - Overdue Amount: ₹{loan.get('overdue_amount', 0):,.0f}"
                f"\n    - Days Overdue: {loan.get('days_overdue', 0)}"
                f"\n    - Loan Amount: ₹{loan.get('loan_amount', 0):,.0f}"
                f"\n    - Status: {loan.get('status', 'ACTIVE')}"
            )
    else:
        loan_details = "\n  No active loans found."

    # Find the most overdue loan for primary focus
    primary_loan = max(loans, key=lambda l: l.get("days_overdue", 0)) if loans else {}
    primary_overdue = f"₹{primary_loan.get('overdue_amount', 0):,.0f}" if primary_loan else "N/A"
    primary_emi = f"₹{primary_loan.get('emi_amount', 0):,.0f}" if primary_loan else "N/A"
    primary_type = primary_loan.get("loan_type", "loan") if primary_loan else "loan"
    primary_days = primary_loan.get("days_overdue", 0) if primary_loan else 0

    if call_purpose == "LOAN_RECOVERY":
        purpose_instructions = f"""
आपका मुख्य काम: {name} को उनके pending payment की याद दिलाना और resolve करने में मदद करना।

Customer को बताने के लिए key details:
- उनके {primary_type} account में {primary_overdue} का overdue payment है
- Payment {primary_days} दिन से pending है
- Monthly EMI {primary_emi} है

Conversation flow:
1. GREETING: अपना परिचय दें, पूछें कि क्या {name} जी से बात हो सकती है
2. Confirm होने के बाद, overdue payment के बारे में बताएं — इस तरह बोलें:
   "{name} जी, आपके {primary_type} account में {primary_overdue} का payment {primary_days} दिन से pending है। आपकी EMI {primary_emi} है। हम आपकी मदद करना चाहते हैं।"
3. उनकी बात सुनें, empathetic रहें
4. Payment options offer करें:
   - UPI payment
   - Net Banking
   - Mobile app
   - Branch visit
5. अगर agree करें, details confirm करें
6. अगर समय चाहिए, callback offer करें
7. अगर supervisor से बात करना चाहें, escalate करें
8. धन्यवाद के साथ call close करें

Hardship के लिए resolution options:
- Partial payment with commitment date
- EMI restructuring (कम monthly amount, लंबा tenure)
- One-time settlement (if authorized)
"""
    elif call_purpose == "PRODUCT_OFFERING":
        purpose_instructions = f"""
Your task: Offer {name} a product suited to their profile.
Customer is in {city}, risk tier: {risk_tier}.
Existing loans: {loan_details}
Offer relevant products: Personal Loan (₹25L, 10.49%), Home Loan Top-up, Balance Transfer, Gold Loan, Credit Card, or Insurance.
"""
    else:  # SERVICE_FOLLOWUP
        purpose_instructions = f"""
Your task: Collect feedback from {name} about their recent experience.
Existing loans: {loan_details}
Ask about their satisfaction, resolve any concerns, inform about new services.
"""

    instructions = f"""आप Ria हैं, VaaniSeva की एक professional और empathetic female collections agent। VaaniSeva एक leading Indian NBFC है।

Customer Account Details (Lakebase database से):
- Customer Name: {name}
- City: {city}
- Phone: {phone}
- Language Preference: {lang_pref}
- Risk Tier: {risk_tier}
- Account Loans: {loan_details}

{purpose_instructions}

Communication guidelines:
- Phone call पर naturally बोलें — Hindi/Hinglish में, जैसे एक real person बोलती है
- हमेशा professional और friendly tone रखें — "जी" suffix use करें
- Customer की financial situation के प्रति empathetic रहें
- कभी aggressive, threatening, या inappropriate language use न करें
- अगर customer upset हों, शांत और understanding रहें
- हर response सिर्फ 1-2 छोटे वाक्य में दें। MAXIMUM 30 शब्द। यह phone call है — लंबे जवाब मत दें!
- Customer जिस language में बोले उसी में respond करें (Hindi, English, or Hinglish)
- RBI Fair Practices Code follow करें — debt info किसी और को disclose न करें
- अगर customer human से बात करना चाहें, acknowledge करें और transfer offer करें

CRITICAL RULES:
- आप LIVE PHONE CALL पर हैं। सिर्फ वही output दें जो आप बोलेंगी।
- Reasoning, thinking, या internal notes include न करें।
- "Let me think", "I should", "The customer is" जैसे phrases use न करें।
- Markdown, bullet points, या formatting use न करें — बस naturally बोलें।
- IMPORTANT: हिंदी शब्द देवनागरी लिपि में लिखें, English शब्द Roman script में।
  ✓ सही: "आपका order confirm हो गया है"
  ✗ गलत: "Aapka order confirm ho gaya hai"
- Greeting से शुरू करें — VaaniSeva से call कर रही हैं यह बताएं।

MOST IMPORTANT RULE — BREVITY:
आपका हर जवाब MAXIMUM 15-20 शब्दों का होना चाहिए। एक phone call पर लोग छोटे जवाब चाहते हैं।
बुरा example: "नमस्ते, मैं Ria बोल रही हूँ VaaniSeva से। आपके Personal Loan account में ₹30,000 का payment 55 दिन से pending है। आपकी EMI ₹10,000 है। हम आपकी मदद करना चाहते हैं। क्या आप payment के बारे में बात कर सकते हैं?"
अच्छा example: "नमस्ते, Ria बोल रही हूँ VaaniSeva से। आपकी EMI pending है, बात कर सकते हैं?"
"""
    return instructions


# ---------------------------------------------------------------------------
# Voice Session (manages Agent + Room connection)
# ---------------------------------------------------------------------------

class VaaniSevaVoiceSession:
    """Manages a LiveKit voice session: room connection + agent pipeline."""

    def __init__(self, room_name: str, customer: dict, loans: list[dict], call_purpose: str):
        self.room_name = room_name
        self.customer = customer
        self.loans = loans
        self.call_purpose = call_purpose
        self.room = rtc.Room()
        self._agent_session: AgentSession | None = None
        self._http_session: aiohttp.ClientSession | None = None
        self._running = False

    async def connect_room(self):
        """Connect to LiveKit room (step 1 — before dialing)."""
        token = generate_token(self.room_name, "vaaniseva-agent")
        self._http_session = aiohttp.ClientSession()
        await self.room.connect(LIVEKIT_URL, token)
        self._running = True
        logger.info(f"Agent connected to room '{self.room_name}'")

    async def start_agent(self):
        """Start the voice agent session (step 2 — AFTER callee picks up).

        This ensures the greeting plays when the customer is actually
        listening, not into an empty room while the phone is ringing.
        """
        instructions = build_collection_instructions(
            self.customer, self.loans, self.call_purpose
        )

        # Auto-greet immediately on enter (Sarvam cookbook pattern)
        class VaaniSevaAgent(Agent):
            async def on_enter(self):
                self.session.generate_reply()

        agent = VaaniSevaAgent(
            instructions=instructions,
            # Reduce endpointing delay — respond faster after user stops speaking
            min_endpointing_delay=0.3,    # default 0.5
            max_endpointing_delay=1.0,    # default 6.0
            stt=sarvam.STT(
                language="hi-IN",         # Fixed to Hindi — faster than auto-detect
                model="saaras:v3",
                api_key=SARVAM_API_KEY,
                http_session=self._http_session,
            ),
            llm=openai_plugin.LLM(
                model="gpt-4.1-nano",
                temperature=0.7,
                max_completion_tokens=100,
            ),
            # Sarvam Bulbul v3 WebSocket Streaming TTS (best practices)
            # - Priya: Tier 1 female voice (CER 0.13%)
            # - PCM 8kHz: lowest overhead for telephony/LiveKit
            # - Pace 1.0, Temp 0.6: natural conversational
            tts=SarvamStreamingTTS(
                api_key=SARVAM_API_KEY,
                model="bulbul:v3",
                speaker="priya",
                target_language_code="hi-IN",
                output_format="linear16",
                sample_rate=8000,
                pace=1.0,
                temperature=0.6,
                http_session=self._http_session,
            ),
        )

        self._agent_session = AgentSession(
            tts_text_transforms=[strip_think_blocks],
        )
        await self._agent_session.start(
            agent=agent,
            room=self.room,
        )

        logger.info(
            f"VaaniSeva agent started for customer '{self.customer.get('name')}' "
            f"in room '{self.room_name}'"
        )

    async def start(self):
        """Connect + start agent (for browser WebRTC calls)."""
        await self.connect_room()
        await self.start_agent()

    async def dial_phone(self, phone_number: str):
        """Place an outbound phone call via LiveKit SIP + Twilio trunk.

        The callee joins the LiveKit room as a SIP participant.
        The agent (already running) handles the conversation.
        """
        if not SIP_OUTBOUND_TRUNK_ID:
            raise ValueError("SIP_OUTBOUND_TRUNK_ID not configured")

        lk = lk_api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        try:
            logger.info(f"Dialing {phone_number} via SIP trunk {SIP_OUTBOUND_TRUNK_ID}")
            await lk.sip.create_sip_participant(
                lk_api.CreateSIPParticipantRequest(
                    room_name=self.room_name,
                    sip_trunk_id=SIP_OUTBOUND_TRUNK_ID,
                    sip_call_to=phone_number,
                    participant_identity=f"phone-{phone_number}",
                    participant_name=self.customer.get("name", "Customer"),
                    wait_until_answered=True,
                    krisp_enabled=True,
                )
            )
            logger.info(f"Call answered by {phone_number}")
            return {"status": "answered", "phone": phone_number}
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Outbound call failed: {error_msg}")
            return {"status": "failed", "error": error_msg, "phone": phone_number}
        finally:
            await lk.aclose()

    async def stop(self):
        """Disconnect and cleanup."""
        self._running = False
        try:
            if self._agent_session:
                await self._agent_session.aclose()
        except Exception:
            pass
        try:
            await self.room.disconnect()
        except Exception:
            pass
        try:
            if self._http_session:
                await self._http_session.close()
        except Exception:
            pass
        _active_agents.pop(self.room_name, None)
        logger.info(f"Voice session stopped for room '{self.room_name}'")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

async def start_voice_agent(
    room_name: str,
    customer: dict,
    loans: list[dict],
    call_purpose: str = "LOAN_RECOVERY",
) -> VaaniSevaVoiceSession:
    """Create and start a voice agent in the given LiveKit room."""
    session = VaaniSevaVoiceSession(room_name, customer, loans, call_purpose)
    _active_agents[room_name] = session
    await session.start()
    return session


def get_active_agent(room_name: str) -> VaaniSevaVoiceSession | None:
    return _active_agents.get(room_name)
