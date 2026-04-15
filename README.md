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

## WhatsApp Setup (Twilio Sandbox + Token Relay)

WhatsApp collections flow uses a **Twilio Function** as a relay between WhatsApp and the Databricks App. This is needed because Databricks Apps are behind SSO — Twilio can't call them directly.

### Architecture

```
Customer Phone (WhatsApp)
    |
    v
Twilio WhatsApp Sandbox (+1 415 523 8886)
    |
    v
Twilio Function (twilio_function.js)
    |  - Concatenates T1+T2+T3+T4 = OAuth token
    |  - Calls POST https://<app>/api/whatsapp/process
    |  - Returns reply to Twilio
    v
Databricks App (VaaniSeva)
    |  - Flow engine: menu, verify account, EMI lookup
    |  - Lakebase queries for customer/loan data
    |  - AI chat via LLM for free-form questions
    v
Reply sent back to customer's WhatsApp
```

### Step 1: Deploy Twilio Function

1. Go to **Twilio Console** > **Functions & Assets** > **Services**
2. Create a new service (e.g., `vaaniseva-wa`)
3. Add a function at path `/whatsapp` — paste the contents of `twilio_function.js`
4. Set environment variables:
   - `APP_HOST` = your Databricks App hostname (e.g., `yatra-voice-agent-984752964297111.11.azure.databricksapps.com`)
   - `SARVAM_KEY` = your Sarvam API key
   - `T1`, `T2`, `T3`, `T4` = OAuth token split (see Step 2)
5. Deploy the function

### Step 2: Generate OAuth Tokens (T1-T4)

Databricks Apps require **user OAuth tokens** (not Service Principal tokens). The token is split into 4 parts because Twilio env vars have a 255-char limit.

```bash
# Generate user token via Databricks CLI
TOKEN=$(databricks auth token --profile <your-profile> | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token length: ${#TOKEN}"

# Split into T1-T4
echo "T1=${TOKEN:0:255}"
echo "T2=${TOKEN:255:255}"
echo "T3=${TOKEN:510:255}"
echo "T4=${TOKEN:765:255}"
```

Set T1, T2, T3, T4 in Twilio Console > Functions > your service > Environment Variables, then **Deploy** the function.

> **Important:** User OAuth tokens expire in **1 hour**. You must regenerate and redeploy T1-T4 tokens before each testing session. For production, use the v2 Twilio Function (`twilio_function.js`) which auto-refreshes via Service Principal.

> **Troubleshooting — 401 / Empty Response `{}`:**
> If WhatsApp returns "Error. Reply menu" and the Twilio Function logs show `App resp: {}`, the token is being rejected. Common causes:
>
> 1. **Using Service Principal (SP) token instead of User token** — Databricks Apps do NOT accept SP OAuth tokens for API access, even with `CAN_USE` or `CAN_MANAGE` permissions. You MUST use a **user OAuth token** generated via `databricks auth token --profile <profile>`.
> 2. **Token expired** — User tokens expire in 1 hour. Regenerate T1-T4 and redeploy.
> 3. **IP ACL blocking** — Check Twilio Function logs for `"Source IP address: x.x.x.x is blocked"`. Add the IP to workspace ACL (see Step 4).
> 4. **Sandbox expired** — Twilio WhatsApp sandbox sessions expire every 72 hours. Rejoin by sending the sandbox keyword.

### Step 3: Configure WhatsApp Sandbox Webhook

1. Go to **Twilio Console** > **Messaging** > **Try it Out** > **Send a WhatsApp Message**
2. Note your sandbox keyword (e.g., `join highest-try`)
3. Set the webhook URL: `https://your-service-xxxx.twil.io/whatsapp`
4. Method: POST

### Step 4: Add Twilio Function IPs to Workspace ACL

Twilio Functions run on dynamic AWS IPs. Add broad ranges to your Databricks workspace IP Access List:

```bash
databricks api post /api/2.0/ip-access-lists --json '{
  "label": "twilio-functions",
  "list_type": "ALLOW",
  "ip_addresses": [
    "3.80.0.0/12",
    "3.92.0.0/14",
    "34.192.0.0/12",
    "34.224.0.0/12",
    "44.192.0.0/11",
    "52.0.0.0/11",
    "54.80.0.0/13",
    "100.24.0.0/13",
    "100.53.0.0/16"
  ]
}'
```

### Step 5: Test

1. Send the sandbox keyword to **+1 415 523 8886** on WhatsApp (rejoin every 72 hours)
2. Send **"hi"** — you should see the collections menu
3. Send **"2"** (EMI check) → enter last 4 digits of account → see loan details from Lakebase

### Automated Token Refresh (via CLI)

To quickly refresh T1-T4 tokens and redeploy the Twilio Function:

```bash
# Set your Twilio v1 credentials
V1_SID="your-twilio-account-sid"
V1_TOKEN="your-twilio-auth-token"
SERVICE_SID="your-twilio-service-sid"
ENV_SID="your-twilio-environment-sid"

# Generate fresh user token
TOKEN=$(databricks auth token --profile <your-profile> | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Split and update
for i in 1 2 3 4; do
  START=$(( (i-1) * 255 ))
  VAL="${TOKEN:$START:255}"
  VAR_SID=$(curl -s "https://serverless.twilio.com/v1/Services/$SERVICE_SID/Environments/$ENV_SID/Variables" \
    -u "$V1_SID:$V1_TOKEN" | python3 -c "
import sys,json
for v in json.load(sys.stdin)['variables']:
    if v['key'] == 'T$i': print(v['sid']); break
")
  curl -s -X POST "https://serverless.twilio.com/v1/Services/$SERVICE_SID/Environments/$ENV_SID/Variables/$VAR_SID" \
    -u "$V1_SID:$V1_TOKEN" -d "Value=$VAL" > /dev/null
  echo "Updated T$i"
done

# Rebuild and deploy (required for env var changes to take effect)
BUILD=$(curl -s -X POST "https://serverless.twilio.com/v1/Services/$SERVICE_SID/Builds" \
  -u "$V1_SID:$V1_TOKEN" \
  --data-urlencode "FunctionVersions=<FV1_SID>" \
  --data-urlencode "FunctionVersions=<FV2_SID>" \
  --data-urlencode "FunctionVersions=<FV3_SID>" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['sid'])")
sleep 10
curl -s -X POST "https://serverless.twilio.com/v1/Services/$SERVICE_SID/Environments/$ENV_SID/Deployments" \
  -u "$V1_SID:$V1_TOKEN" -d "BuildSid=$BUILD"
echo "Deployed with fresh tokens"
```

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
