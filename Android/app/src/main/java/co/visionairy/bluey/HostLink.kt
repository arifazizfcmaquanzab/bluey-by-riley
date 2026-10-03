package co.visionairy.bluey

import android.os.Handler
import android.os.Looper
import android.util.Base64
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/** Certificate pin is checked in the TLS handshake, BEFORE transmitting the pairing key. */
class HostLink(private val onPacket: (JSONObject) -> Unit, private val onStatus: (Boolean, String) -> Unit) {
    private val main = Handler(Looper.getMainLooper())
    private val sender = Executors.newSingleThreadExecutor()
    private val waiting = ConcurrentHashMap<String, (JSONObject?) -> Unit>()
    @Volatile private var generation = 0
    @Volatile private var socket: Socket? = null
    @Volatile private var output: OutputStream? = null
    @Volatile var connected = false
        private set
    private val heartbeat = object : Runnable {
        override fun run() {
            if (connected) { send(JSONObject().put("command", "ping")); main.postDelayed(this, 15000) }
        }
    }

    fun connect(code: String) {
        disconnect()
        val current = generation
        Thread {
            var local: Socket? = null
            try {
                val pair = JSONObject(String(Base64.decode(code.trim(), Base64.URL_SAFE), Charsets.UTF_8))
                require(pair.getInt("version") == 1) { "Unsupported pairing version" }
                val host = pair.getString("host")
                val port = pair.getInt("port")
                val fingerprint = pair.getString("fingerprint").lowercase()
                val key = pair.getString("key")
                require(host.matches(Regex("[0-9.]+")) && port in 1..65535 &&
                        fingerprint.matches(Regex("[0-9a-f]{64}")) && key.matches(Regex("[0-9a-f]{32}"))) { "Invalid pairing code" }
                val trust = object : X509TrustManager {
                    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = throw CertificateException("Not a server")
                    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                        if (chain.isEmpty()) throw CertificateException("Missing host certificate")
                        chain[0].checkValidity()
                        val actual = MessageDigest.getInstance("SHA-256").digest(chain[0].encoded)
                            .joinToString("") { "%02x".format(it.toInt() and 255) }
                        if (!MessageDigest.isEqual(actual.toByteArray(), fingerprint.toByteArray()))
                            throw CertificateException("PC certificate does not match pairing code")
                    }
                }
                val context = SSLContext.getInstance("TLS")
                context.init(null, arrayOf<TrustManager>(trust), SecureRandom())
                val connection = context.socketFactory.createSocket()
                local = connection
                synchronized(this) {
                    if (current != generation) { connection.close(); return@Thread }
                    socket = connection
                }
                connection.connect(InetSocketAddress(host, port), 8000)
                connection.soTimeout = 10000
                (connection as SSLSocket).startHandshake()
                val writer = connection.getOutputStream()
                writer.write((JSONObject().put("command", "pair").put("pairingKey", key).toString() + "\n").toByteArray())
                writer.flush()
                val input = BufferedInputStream(connection.getInputStream())
                val reply = readPacket(input)
                require(reply.optString("command") == "paired") { "PC rejected pairing. Copy its current code." }
                synchronized(this) {
                    if (current != generation) { connection.close(); return@Thread }
                    output = writer
                    connected = true
                }
                connection.soTimeout = 90000
                main.post {
                    if (current == generation) {
                        onStatus(true, "Connected to " + reply.optString("hello", "Windows"))
                        main.post(heartbeat)
                    }
                }
                while (current == generation) {
                    val packet = readPacket(input)
                    main.post {
                        if (current == generation) {
                            val callback = waiting.remove(packet.optString("callID"))
                            if (callback != null) callback(packet) else onPacket(packet)
                        }
                    }
                }
            } catch (_: Exception) {
                main.post { if (current == generation) { disconnect(); onStatus(false, "Connection failed. Check Wi-Fi, firewall, and current pairing code.") } }
            } finally { try { local?.close() } catch (_: Exception) {} }
        }.start()
    }

    private fun readPacket(input: BufferedInputStream): JSONObject {
        val buffer = ByteArrayOutputStream()
        while (buffer.size() <= 4 * 1024 * 1024) {
            val byte = input.read()
            if (byte == -1) throw java.io.EOFException()
            if (byte == 10) return JSONObject(buffer.toString("UTF-8"))
            buffer.write(byte)
        }
        throw IllegalArgumentException("PC packet too large")
    }

    fun send(packet: JSONObject) {
        val current = generation
        val bytes = (packet.toString() + "\n").toByteArray(Charsets.UTF_8)
        require(bytes.size <= 4 * 1024 * 1024)
        sender.execute {
            if (current != generation) return@execute
            try { output?.let { it.write(bytes); it.flush() } }
            catch (_: Exception) { main.post { if (current == generation) { disconnect(); onStatus(false, "PC disconnected") } } }
        }
    }

    fun request(packet: JSONObject, callback: (JSONObject?) -> Unit) {
        if (!connected) { callback(null); return }
        val id = UUID.randomUUID().toString()
        waiting[id] = callback
        send(packet.put("callID", id))
        main.postDelayed({ waiting.remove(id)?.invoke(null) }, 45000)
    }

    @Synchronized fun disconnect() {
        generation++
        connected = false
        main.removeCallbacks(heartbeat)
        try { socket?.close() } catch (_: Exception) {}
        socket = null
        output = null
        val callbacks = waiting.values.toList()
        waiting.clear()
        callbacks.forEach { it(null) }
    }

    fun destroy() { disconnect(); sender.shutdownNow() }
}
