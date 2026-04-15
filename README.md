# VaaniSeva — Sovereign AI Voice Agent for Indian BFSI Collections

Real-time AI voice agent that makes outbound phone calls for loan collections, speaking naturally in Hindi/Hinglish with empathy and RBI compliance.

**Stack:** Sarvam AI (STT/TTS) + GPT-4.1-nano (LLM) + LiveKit (WebRTC/SIP) + Databricks Lakebase (data) + FastAPI (app)

## Architecture

```
Phone (PSTN)  -->  Twilio SIP Trunk  -->  LiveKit SIP Bridge  -->  LiveKit Room
                                                                       |
                                                                 VaaniSeva Agent
                                                              +--------+--------+
                                                              |  Sarvam STT     |
                                                              |  saaras:v3      |
                                                              |  (streaming)    |
                                                              +-----------------+
                                                              |  GPT-4.1-nano   |
                                                              |  (with customer |
                                                              |   data context) |
                                                              +-----------------+
                                                              |  Sarvam TTS     |
                                                              |  bulbul:v2      |
                                                              |  anushka voice  |
                                                              +--------+--------+
                                                                       |
Phone (PSTN)  <--  Twilio SIP Trunk  <--  LiveKit SIP Bridge  <--  LiveKit Room
                                                                       |
                                                              Databricks Lakebase
                                                              (customer profiles,
                                                               loans, payments)
```

## Features

- **Real-time phone calls** -- outbound calls to Indian mobile numbers via LiveKit SIP + Twilio
- **Browser voice** -- WebRTC-based voice calls via browser microphone
- **Sarvam AI voice** -- realistic Hindi/Indian language STT (saaras:v3) and TTS (bulbul:v2, anushka voice)
- **Smart agent brain** -- GPT-4o with full customer context (loans, EMI, overdue amounts) from Lakebase
- **WhatsApp collections** -- multi-step WhatsApp flow via Twilio
- **Web dashboard** -- 4-tab UI: Customer Simulator, Agent Live View (planned), Quality Auditor (planned), WhatsApp

> **Note:** Agent Live View and Quality Auditor tabs are visible in the UI but not yet implemented. They are planned for a future release.
- **Data sovereignty** -- Sarvam AI is India-hosted, Lakebase on Azure Central India

## Prerequisites

| Service | Purpose | Sign Up |
|---------|---------|---------|
| **Databricks** | Workspace + Lakebase (customer data) | Your existing workspace |
| **Sarvam AI** | STT (saaras:v3) + TTS (bulbul:v2) | https://dashboard.sarvam.ai |
| **OpenAI** | LLM brain (GPT-4o) | https://platform.openai.com |
| **LiveKit Cloud** | WebRTC rooms + SIP bridge | https://livekit.io/cloud |
| **Twilio** | SIP trunk (PSTN) + WhatsApp | https://console.twilio.com |

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/sarbaniAi/enhance-vaaniseva.git
cd enhance-vaaniseva
cp env.template .env
# Edit .env with your API keys
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up Lakebase (customer data)

```bash
# Run the setup script to create Lakebase project and seed data
./setup.sh

# Or use the notebooks in order:
# notebooks/00_setup_lakebase.py
# notebooks/01_generate_synthetic_data.py
```

### 4. Run locally

```bash
# Set SSL certs (macOS)
export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")

# Load env and start
set -a && source .env && set +a
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

### 5. Deploy to Databricks Apps

```bash
databricks bundle deploy --target dev
```

## Phone Call Setup (LiveKit SIP + Twilio)

This enables real outbound phone calls to mobile numbers.

### Step 1: Create Twilio SIP Trunk

```bash
# Via Twilio Console: Elastic SIP Trunking > Trunks > Create
# Or via API:
curl -X POST "https://trunking.twilio.com/v1/Trunks" \
  -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -d "FriendlyName=vaaniseva-livekit" \
  -d "DomainName=your-trunk-name.pstn.twilio.com"
```

### Step 2: Create SIP credentials

```bash
# Create credential list
curl -X POST "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/SIP/CredentialLists.json" \
  -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -d "FriendlyName=vaaniseva-creds"

# Add username/password (use the CL_SID from above)
curl -X POST "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/SIP/CredentialLists/$CL_SID/Credentials.json" \
  -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -d "Username=vaaniseva_sip" \
  -d "Password=YourStrongPassword"

# Attach to trunk termination
curl -X POST "https://trunking.twilio.com/v1/Trunks/$TRUNK_SID/CredentialLists" \
  -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -d "CredentialListSid=$CL_SID"

# Associate phone number
curl -X POST "https://trunking.twilio.com/v1/Trunks/$TRUNK_SID/PhoneNumbers" \
  -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
  -d "PhoneNumberSid=$PHONE_SID"
```

### Step 3: Register outbound trunk with LiveKit

```bash
# Install LiveKit CLI
brew install livekit-cli   # macOS

# Set credentials
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=your-key
export LIVEKIT_API_SECRET=your-secret

# Create trunk config
cat > outbound-trunk.json << 'EOF'
{
  "trunk": {
    "name": "VaaniSeva Twilio Outbound",
    "address": "your-trunk-name.pstn.twilio.com",
    "numbers": ["+1XXXXXXXXXX"],
    "auth_username": "vaaniseva_sip",
    "auth_password": "YourStrongPassword"
  }
}
EOF

lk sip outbound create outbound-trunk.json
# Save the SIP_OUTBOUND_TRUNK_ID (e.g., ST_xxxx) in your .env
```

### Step 4: Test

Open http://localhost:8000, select a customer, enter a phone number, click **"Phone Call (SIP)"**.

## Project Structure

```
enhance-vaaniseva/
|-- app.py                          # FastAPI entrypoint
|-- app.yaml                        # Databricks App config
|-- requirements.txt                # Python dependencies
|-- env.template                    # Environment variables template
|-- setup.sh                        # Automated setup script
|
|-- vaaniseva/                      # Core application
|   |-- config.py                   # Environment config + constants
|   |-- db.py                       # Lakebase connection pool (OAuth)
|   |-- models.py                   # Pydantic models
|   |
|   |-- voice/                      # Voice AI pipeline
|   |   |-- livekit_agent.py        # LiveKit agent (STT->LLM->TTS)
|   |   |-- sarvam_tts_rest.py      # Custom Sarvam TTS (REST, WAV)
|   |   |-- stt_client.py           # Sarvam STT REST client
|   |   |-- tts_client.py           # Sarvam TTS REST client
|   |   +-- audio_utils.py          # Audio utilities
|   |
|   |-- agent/                      # Agent brain
|   |   |-- brain.py                # LLM call logic
|   |   |-- call_flow.py            # Call session state machine
|   |   |-- system_prompts.py       # Stage-specific prompts
|   |   +-- escalation.py           # Escalation detection
|   |
|   |-- routes/                     # API endpoints
|   |   |-- livekit_api.py          # LiveKit voice + SIP phone calls
|   |   |-- call_api.py             # Text-based call simulation
|   |   |-- customer_api.py         # Customer data API
|   |   |-- whatsapp_api.py         # WhatsApp collections flow
|   |   |-- audit_api.py            # Quality audit API
|   |   +-- data_api.py             # Dashboard data API
|   |
|   +-- retrieval/                  # Data retrieval
|       |-- genie.py                # Lakebase SQL queries
|       |-- rag.py                  # Vector search RAG
|       +-- hybrid.py               # Hybrid retrieval
|
|-- static/                         # Web UI
|   |-- index.html
|   |-- styles.css
|   +-- js/                         # Frontend modules
|
|-- notebooks/                      # Databricks setup notebooks
|-- scripts/                        # Utility scripts
|-- twilio_function.js              # Twilio Function (WhatsApp relay)
+-- twilio_audio_function.js        # Twilio Function (TTS audio)
```

## How It Works

### Phone Call Flow

1. User clicks **"Phone Call (SIP)"** in the web UI
2. FastAPI loads customer + loan data from **Databricks Lakebase**
3. **LiveKit room** is created, agent connects via WebSocket
4. **LiveKit SIP** dials the phone via **Twilio SIP Trunk**
5. Phone rings, customer picks up, **then agent starts** (dial-then-start pattern)
6. **GPT-4.1-nano** generates greeting with customer-specific loan data (Devanagari instructions)
7. **Sarvam TTS** bulbul:v3 (Priya voice, PCM 8kHz) converts text to realistic Hindi speech
8. Customer speaks, **Sarvam STT** saaras:v3 (streaming WebSocket, hi-IN) transcribes
9. **GPT-4.1-nano** responds with context, **Sarvam TTS** speaks it back
10. Conversation continues until resolution or escalation

### WhatsApp Collections Flow

```
Customer WhatsApp                Twilio                    Databricks App
     |                             |                            |
     |-- "hi" ------------------>  |                            |
     |                             |-- webhook (blocked by SSO) |
     |                             |                            |
     |                             |-- Twilio Function -------> |
     |                             |   (relay with OAuth)       |
     |                             |   POST /api/whatsapp/process
     |                             |                            |
     |                             |                  +---------+---------+
     |                             |                  | Flow Engine       |
     |                             |                  | 1. Menu           |
     |                             |                  | 2. Verify Account |
     |                             |                  |    (Lakebase)     |
     |                             |                  | 3. Show EMI/Loans |
     |                             |                  | 4. Payment Link   |
     |                             |                  | 5. Restructuring  |
     |                             |                  | 6. AI Chat (LLM)  |
     |                             |                  +---------+---------+
     |                             |                            |
     |                             | <-- reply JSON ---------- |
     |                             |                            |
     | <-- WhatsApp message ------ |                            |
```

**How it works:**

1. Customer sends a WhatsApp message to the Twilio sandbox number
2. Twilio can't reach Databricks App directly (SSO blocks webhooks)
3. A **Twilio Function** (`twilio_function.js`) acts as a relay:
   - Authenticates via **Service Principal** (client_credentials OAuth)
   - Forwards the message to `POST /api/whatsapp/process`
   - Returns the reply to Twilio for delivery
4. The **Flow Engine** (`whatsapp_api.py`) processes the message:
   - **Menu**: Shows options (Payment, EMI Check, Restructuring, Callback, AI Chat)
   - **Account Verification**: Looks up customer by last 4 digits in **Lakebase**
   - **Loan Details**: Fetches EMI, overdue amount, days from Lakebase
   - **AI Chat**: Free-form conversation via **Sarvam-M / GPT-4o** LLM
   - **Voice Notes**: Transcribed via **Sarvam STT** (saaras:v3)

**WhatsApp Menu Flow:**
```
Customer sends "hi"
  --> Menu: 1.Payment  2.EMI  3.Restructure  4.Callback  5.AI Chat
Customer sends "2"
  --> "Share your last 4 digits of account number."
Customer sends "6543"
  --> Lakebase lookup --> "Amit Patel ji, your Personal Loan EMI: ₹15,000. Overdue: ₹30,000 (45 days)"
```

**Setup:** Deploy `twilio_function.js` to Twilio Functions, set env vars (SP_CLIENT_ID, SP_CLIENT_SECRET, DB_HOST, APP_HOST), configure WhatsApp sandbox webhook to the function URL.

### Why This Architecture?

- **LiveKit SIP bridge** -- both agent and phone make *outbound* connections to LiveKit Cloud. No incoming webhooks needed, works behind Databricks Apps SSO/firewall.
- **Twilio Function relay** -- WhatsApp webhooks can't reach Databricks Apps (SSO). The relay function authenticates via Service Principal and forwards requests.
- **Custom Sarvam SDK streaming TTS** -- the `livekit-plugins-sarvam` WebSocket TTS has a format bug (sends MP3, declares WAV). `sarvam_tts_streaming.py` uses the SDK `convert_stream()` for progressive PCM audio delivery.
- **Dial-then-start** -- agent session starts *after* the phone is answered, so the greeting plays to a live caller, not into an empty room.
- **Customer data in LLM instructions** -- all Lakebase data (name, loans, overdue amounts) is injected into the LLM system prompt, following the [Sarvam cookbook pattern](https://docs.sarvam.ai/api-reference-docs/cookbook/example-voice-agents/collection-agent).

## Latency

| Component | Latency |
|-----------|---------|
| Sarvam STT (streaming WebSocket) | ~0.5s |
| GPT-4.1-nano (streaming) | ~0.5-1.0s |
| Sarvam TTS bulbul:v3 (SDK streaming, TTFB) | ~0.5s |
| VAD endpointing | ~0.3-1.0s |
| **Perceived latency (user stops speaking → hears response)** | **~3-5s** |

> To reduce further: ask Sarvam team to enable WebSocket streaming TTS endpoint (`wss://api.sarvam.ai/v1/text-to-speech/stream`) for ~200ms TTFB.

## Customization

### Change the agent persona
Edit `build_collection_instructions()` in `vaaniseva/voice/livekit_agent.py`.

### Change TTS voice
Modify `SarvamStreamingTTS` parameters: `speaker` (priya, ishita, shubh, ratan, etc.), `target_language_code` (hi-IN, bn-IN, ta-IN, etc.). See Bulbul v3 Best Practices Guide for recommended speakers per language.

### Use different LLM
Replace `openai_plugin.LLM(model="gpt-4o")` with any OpenAI-compatible LLM.

### Add new call purposes
Add new `elif call_purpose == "YOUR_PURPOSE"` blocks in `build_collection_instructions()`.

## References

- [Sarvam AI Collection Agent Cookbook](https://docs.sarvam.ai/api-reference-docs/cookbook/example-voice-agents/collection-agent)
- [LiveKit Telephony Docs](https://docs.livekit.io/telephony/)
- [LiveKit + Twilio SIP Setup](https://docs.livekit.io/telephony/start/providers/twilio/)
- [LiveKit Outbound Calls](https://docs.livekit.io/telephony/making-calls/outbound-calls/)
