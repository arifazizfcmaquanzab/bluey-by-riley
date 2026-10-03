package co.visionairy.bluey

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import android.util.Base64
import okhttp3.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.math.max

/** Foreground-only audio. Stop/background/disconnect immediately releases the microphone. */
class LiveVoice(private val context: Context, private val link: HostLink,
                private val onState: (String) -> Unit, private val onCaption: (String) -> Unit) {
    private val main = Handler(Looper.getMainLooper())
    private val client = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).pingInterval(20, TimeUnit.SECONDS).build()
    @Volatile private var socket: WebSocket? = null
    @Volatile private var recording = false
    @Volatile private var recorder: AudioRecord? = null
    @Volatile private var bytesSinceCommit = 0L
    private var micThread: Thread? = null
    private var generation = 0
    private var transcript = ""
    private var sleepAfterReply = false
    private var awaitingCommit = false
    private var ready = false
    private var responseActive = false
    var active = false
        private set

    fun wake() {
        if (active) return
        if (!link.connected) { onState("Connect to Windows first"); return }
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            onState("Allow microphone access first"); return
        }
        active = true
        val current = ++generation
        onState("Starting voice…")
        link.request(JSONObject().put("command", "realtimeToken")) { result ->
            if (current != generation || !active) return@request
            if (result == null || result.optString("command") != "realtimeToken") {
                fail(result?.optString("text", "Voice could not start") ?: "Windows did not reply")
                return@request
            }
            val token = result.optString("text")
            val model = result.optString("model", "gpt-realtime-2.1")
            val url = HttpUrl.Builder().scheme("https").host("api.openai.com").addPathSegments("v1/realtime")
                .addQueryParameter("model", model).build()
            val request = Request.Builder().url(url).header("Authorization", "Bearer $token").build()
            socket = client.newWebSocket(request, object : WebSocketListener() {
                override fun onMessage(webSocket: WebSocket, text: String) {
                    main.post {
                        if (current == generation && socket === webSocket) {
                            try { handle(JSONObject(text)) } catch (_: Exception) { fail("Invalid voice response") }
                        }
                    }
                }
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    main.post { if (current == generation) fail("Voice connection failed. Check API billing, model access, and internet.") }
                }
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    main.post { if (current == generation) fail("Voice disconnected") }
                }
            })
        }
    }

    private fun send(event: JSONObject) { socket?.send(event.toString()) }

    private fun startMic() {
        val minimum = AudioRecord.getMinBufferSize(24000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (minimum <= 0) { fail("This phone does not support 24 kHz microphone audio"); return }
        try {
            if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                fail("Microphone permission denied"); return
            }
            val audio = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 24000,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(minimum, 9600))
            recorder = audio
            if (audio.state != AudioRecord.STATE_INITIALIZED) { fail("Microphone initialization failed"); return }
            audio.startRecording()
            recording = true
            val current = generation
            micThread = Thread {
                val bytes = ByteArray(4800)
                while (recording && current == generation) {
                    val count = audio.read(bytes, 0, bytes.size)
                    if (count <= 0) {
                        main.post { if (current == generation) fail("Microphone stopped") }
                        break
                    }
                    bytesSinceCommit += count
                    val accepted = socket?.send(JSONObject().put("type", "input_audio_buffer.append")
                        .put("audio", Base64.encodeToString(bytes, 0, count, Base64.NO_WRAP)).toString()) ?: false
                    if (!accepted) {
                        main.post { if (current == generation) fail("Voice upload stopped") }
                        break
                    }
                }
            }.also { it.start() }
        } catch (_: Exception) { fail("Could not start microphone") }
    }

    fun beginAsk() {
        if (!ready) { onState("Tap Wake first and wait for Listening"); return }
        if (responseActive) { send(JSONObject().put("type", "response.cancel")); responseActive = false }
        onCaption("")
        onState("Asking, release to get a reply")
    }

    fun endAsk() {
        if (!ready || awaitingCommit) return
        if (bytesSinceCommit < 4800) { onState("Speak for at least a moment, then release"); return }
        awaitingCommit = true
        bytesSinceCommit = 0
        onState("Thinking…")
        send(JSONObject().put("type", "input_audio_buffer.commit"))
    }

    fun askText(text: String) {
        if (!ready || text.isBlank()) { onState("Wake voice first"); return }
        if (responseActive) send(JSONObject().put("type", "response.cancel"))
        send(JSONObject().put("type", "conversation.item.create").put("item", JSONObject()
            .put("type", "message").put("role", "user").put("content", JSONArray().put(JSONObject()
                .put("type", "input_text").put("text", text.take(4000))))))
        send(JSONObject().put("type", "response.create"))
        onState("Thinking…")
    }

    private fun handle(event: JSONObject) {
        when (event.optString("type")) {
            "session.created" -> {
                ready = true
                startMic()
                if (recording) { link.send(JSONObject().put("command", "awake")); onState("Listening, microphone on") }
            }
            "input_audio_buffer.committed" -> if (awaitingCommit) {
                awaitingCommit = false
                send(JSONObject().put("type", "response.create"))
            }
            "response.created" -> { transcript = ""; responseActive = true }
            "response.output_text.delta" -> {
                transcript += event.optString("delta")
                onCaption(transcript)
                link.send(JSONObject().put("command", "caption").put("text", transcript))
            }
            "response.done" -> {
                responseActive = false
                link.send(JSONObject().put("command", "captionDone").put("text", transcript))
                val calls = mutableListOf<JSONObject>()
                val output = event.optJSONObject("response")?.optJSONArray("output") ?: JSONArray()
                for (i in 0 until output.length()) {
                    val item = output.optJSONObject(i) ?: continue
                    if (item.optString("type") == "function_call") calls.add(item)
                }
                if (calls.isNotEmpty()) runTools(calls, 0, generation)
                else if (sleepAfterReply) sleep() else onState("Listening, microphone on")
            }
            "error" -> {
                awaitingCommit = false
                val error = event.optJSONObject("error")
                if (error?.optString("code") == "input_audio_buffer_commit_empty") onState("No audio captured. Hold and speak again.")
                else fail("Voice API error. Check the PC model setting and API access.")
            }
        }
    }

    private fun runTools(calls: List<JSONObject>, index: Int, current: Int) {
        if (current != generation || !ready) return
        if (index >= calls.size) { send(JSONObject().put("type", "response.create")); return }
        onState("Checking the PC…")
        val call = calls[index]
        if (call.optString("name") == "go_to_sleep") sleepAfterReply = true
        link.request(JSONObject().put("command", "tool").put("tool", call.optString("name"))
            .put("text", call.optString("arguments", "{}"))) { result ->
            if (current != generation || !ready) return@request
            send(JSONObject().put("type", "conversation.item.create").put("item", JSONObject()
                .put("type", "function_call_output").put("call_id", call.optString("call_id"))
                .put("output", result?.optString("text", "Tool failed") ?: "PC did not reply")))
            val image = result?.optString("image") ?: ""
            if (image.isNotEmpty()) send(JSONObject().put("type", "conversation.item.create").put("item", JSONObject()
                .put("type", "message").put("role", "user").put("content", JSONArray().put(JSONObject()
                    .put("type", "input_image").put("image_url", "data:image/jpeg;base64,$image")))))
            runTools(calls, index + 1, current)
        }
    }

    private fun fail(message: String) { sleep(); onState(message) }

    fun sleep() {
        generation++
        active = false
        ready = false
        awaitingCommit = false
        responseActive = false
        sleepAfterReply = false
        recording = false
        socket?.cancel()
        socket = null
        try { recorder?.stop() } catch (_: Exception) {}
        micThread?.join(500)
        recorder?.release()
        recorder = null
        micThread = null
        bytesSinceCommit = 0
        if (link.connected) link.send(JSONObject().put("command", "asleep"))
        onState("Asleep, microphone off")
    }

    fun destroy() { sleep(); client.dispatcher.executorService.shutdown(); client.connectionPool.evictAll() }
}
