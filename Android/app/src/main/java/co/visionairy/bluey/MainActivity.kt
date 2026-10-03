package co.visionairy.bluey

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.*
import org.json.JSONObject
import kotlin.math.min

class MainActivity : Activity() {
    private lateinit var link: HostLink
    private lateinit var voice: LiveVoice
    private lateinit var status: TextView
    private lateinit var face: FaceView
    private lateinit var wake: Button
    private lateinit var code: EditText
    private var pendingWake = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or WindowManager.LayoutParams.FLAG_SECURE)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 18, 24, 18)
            setBackgroundColor(Color.rgb(255, 249, 235))
        }
        // Insets matter on target SDK 35, where edge-to-edge is enforced.
        root.setOnApplyWindowInsetsListener { view, insets ->
            view.setPadding(24 + insets.systemWindowInsetLeft, 18 + insets.systemWindowInsetTop,
                24 + insets.systemWindowInsetRight, 18 + insets.systemWindowInsetBottom)
            insets
        }
        root.addView(TextView(this).apply { text = "Bluey + Windows"; textSize = 24f })
        code = EditText(this).apply {
            hint = "Paste the Windows pairing code"
            setSingleLine(true)
            setText(getPreferences(MODE_PRIVATE).getString("pair", ""))
        }
        root.addView(code)
        root.addView(Button(this).apply {
            text = "Connect to PC"
            setOnClickListener {
                voice.sleep()
                getPreferences(MODE_PRIVATE).edit().putString("pair", code.text.toString().trim()).apply()
                status.text = "Connecting…"
                link.connect(code.text.toString())
            }
        })
        status = TextView(this).apply { text = "Connect both devices to the same private Wi-Fi"; textSize = 15f }
        root.addView(status)
        face = FaceView(this)
        root.addView(face, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        val caption = TextView(this).apply { textSize = 18f; maxLines = 4 }
        root.addView(caption)
        link = HostLink({ packet ->
            packet.optJSONObject("face")?.let { face.update(it) }
            if (packet.optString("command") == "sleep") { voice.sleep(); wake.text = "Wake" }
        }, { connected, message ->
            status.text = message
            if (!connected) { voice.sleep(); wake.text = "Wake" }
        })
        voice = LiveVoice(this, link, { state -> status.text = state; wake.text = if (voice.active) "Sleep" else "Wake" }, { caption.text = it })
        val buttons = LinearLayout(this)
        wake = Button(this).apply {
            text = "Wake"
            setOnClickListener {
                if (voice.active) voice.sleep()
                else if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) voice.wake()
                else { pendingWake = true; requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1) }
            }
        }
        buttons.addView(wake, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        buttons.addView(Button(this).apply {
            text = "Hold to ask"
            setOnTouchListener { view, event ->
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> voice.beginAsk()
                    MotionEvent.ACTION_UP -> { voice.endAsk(); view.performClick() }
                    MotionEvent.ACTION_CANCEL -> voice.sleep()
                }
                true
            }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(buttons)
        val question = EditText(this).apply { hint = "Or type a question"; setSingleLine(true) }
        val textRow = LinearLayout(this)
        textRow.addView(question, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        textRow.addView(Button(this).apply { text = "Ask"; setOnClickListener { voice.askText(question.text.toString()); question.text.clear() } })
        root.addView(textRow)
        root.addView(TextView(this).apply { text = "While awake, your mic streams to OpenAI. Sleep stops it. Keep this app open. API charges apply."; textSize = 12f })
        setContentView(root)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1 && pendingWake) {
            pendingWake = false
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) voice.wake()
            else status.text = "Microphone denied. Enable it in Android Settings."
        }
    }

    override fun onPause() {
        super.onPause()
        if (::voice.isInitialized) { voice.sleep(); link.disconnect(); wake.text = "Wake"; status.text = "Paused. Reconnect to PC when you return." }
    }
    override fun onDestroy() {
        if (::voice.isInitialized) { voice.destroy(); link.destroy() }
        super.onDestroy()
    }
}

class FaceView(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var gazeX = 0f
    private var gazeY = 0f
    private var mood = "resting"
    fun update(face: JSONObject) {
        gazeX = face.optDouble("gazeX", 0.0).toFloat().coerceIn(-1f, 1f)
        gazeY = face.optDouble("gazeY", 0.0).toFloat().coerceIn(-1f, 1f)
        mood = face.optString("mood", "resting")
        invalidate()
    }
    override fun onDraw(canvas: Canvas) {
        val radius = min(width, height) * 0.40f
        val cx = width / 2f
        val cy = height / 2f
        paint.color = Color.rgb(99, 107, 232)
        canvas.drawOval(cx - radius, cy - radius * .92f, cx + radius, cy + radius, paint)
        for (offset in floatArrayOf(-.36f, .36f)) {
            val ex = cx + radius * offset
            val ey = cy - radius * .20f
            paint.color = Color.WHITE
            canvas.drawOval(ex - radius * .26f, ey - radius * .34f, ex + radius * .26f, ey + radius * .34f, paint)
            paint.color = Color.rgb(23, 28, 55)
            canvas.drawCircle(ex + gazeX * radius * .10f, ey + gazeY * radius * .12f, radius * .12f, paint)
            paint.color = Color.WHITE
            canvas.drawCircle(ex + gazeX * radius * .10f - radius * .035f, ey + gazeY * radius * .12f - radius * .04f, radius * .035f, paint)
        }
        paint.color = Color.rgb(23, 28, 55)
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = radius * .035f
        canvas.drawArc(cx - radius * .22f, cy + radius * .20f, cx + radius * .22f, cy + radius * .48f, 0f, 180f, false, paint)
        paint.style = Paint.Style.FILL
        if (mood == "listening") {
            paint.color = Color.rgb(53, 163, 114)
            canvas.drawCircle(cx + radius * .76f, cy - radius * .70f, radius * .075f, paint)
        }
    }
}
