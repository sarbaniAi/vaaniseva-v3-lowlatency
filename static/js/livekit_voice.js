/**
 * VaaniSeva — LiveKit WebRTC voice client.
 *
 * Handles browser ↔ LiveKit Cloud audio for real-time voice calls.
 * The VaaniSeva agent runs server-side and joins the same LiveKit room.
 */
const LiveKitVoice = (() => {
    let room = null;
    let localAudioTrack = null;
    let isConnected = false;
    let currentRoomName = null;
    let currentCallId = null;

    /**
     * Start a voice call: create room + agent, connect browser mic.
     */
    async function startCall(customerId, callPurpose) {
        if (isConnected) {
            console.warn('Already in a LiveKit call');
            return null;
        }

        // Check if LiveKit client SDK loaded
        if (typeof LivekitClient === 'undefined') {
            throw new Error('LiveKit client SDK not loaded. Check network/firewall — CDN may be blocked.');
        }

        // 1. Request room + token from backend (also starts the agent)
        const data = await App.api('/api/livekit/join', {
            method: 'POST',
            body: JSON.stringify({
                customer_id: customerId,
                call_purpose: callPurpose,
            }),
        });

        if (data.error) {
            throw new Error(data.error);
        }

        const { token, url, room_name, call_id, customer_name, greeting, stage } = data;
        currentRoomName = room_name;
        currentCallId = call_id;

        // 2. Create LiveKit Room (from livekit-client SDK loaded via CDN)
        room = new LivekitClient.Room({
            adaptiveStream: true,
            dynacast: true,
        });

        // Handle remote audio (agent's voice)
        room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
            if (track.kind === LivekitClient.Track.Kind.Audio) {
                const audioEl = track.attach();
                audioEl.id = 'livekit-agent-audio';
                // Remove previous audio element if exists
                const existing = document.getElementById('livekit-agent-audio');
                if (existing) existing.remove();
                document.getElementById('agent-audio-container')?.appendChild(audioEl);
            }
        });

        room.on(LivekitClient.RoomEvent.Disconnected, () => {
            _onDisconnected();
        });

        // 3. Connect to LiveKit room
        await room.connect(url, token);

        // 4. Publish local microphone
        await room.localParticipant.setMicrophoneEnabled(true);

        isConnected = true;

        return {
            call_id,
            room_name,
            customer_name,
            greeting,
            stage,
        };
    }

    /**
     * End the voice call — disconnect and notify backend.
     */
    async function endCall() {
        if (!isConnected || !room) return;

        try {
            // Notify backend to stop agent
            await App.api('/api/livekit/leave', {
                method: 'POST',
                body: JSON.stringify({
                    room_name: currentRoomName,
                    call_id: currentCallId,
                }),
            });
        } catch (e) {
            console.error('Leave API error:', e);
        }

        _disconnect();
    }

    /**
     * Toggle microphone mute.
     */
    async function toggleMute() {
        if (!room || !isConnected) return false;
        const enabled = room.localParticipant.isMicrophoneEnabled;
        await room.localParticipant.setMicrophoneEnabled(!enabled);
        return !enabled; // returns new state: true = unmuted
    }

    /**
     * Check if LiveKit is configured on the backend.
     */
    async function checkAvailable() {
        try {
            const data = await App.api('/api/livekit/status');
            return data.configured === true;
        } catch {
            return false;
        }
    }

    function getCallId() { return currentCallId; }
    function getRoomName() { return currentRoomName; }
    function connected() { return isConnected; }

    // --- Internal ---

    function _disconnect() {
        if (room) {
            room.disconnect();
            room = null;
        }
        isConnected = false;
        currentRoomName = null;
        currentCallId = null;

        // Clean up agent audio element
        const audioEl = document.getElementById('livekit-agent-audio');
        if (audioEl) audioEl.remove();
    }

    function _onDisconnected() {
        isConnected = false;
        currentRoomName = null;
        // Notify UI
        if (typeof CustomerSim !== 'undefined' && CustomerSim.onLiveKitDisconnected) {
            CustomerSim.onLiveKitDisconnected();
        }
    }

    return {
        startCall,
        endCall,
        toggleMute,
        checkAvailable,
        getCallId,
        getRoomName,
        connected,
    };
})();
