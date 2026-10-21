"""Web server for the CCMS support agent. Run:  uvicorn app:app --host 0.0.0.0 --port 8000

The BROWSER does the audio (speech-to-text and text-to-speech, see static/index.html).
This server only moves TEXT, and streams the reply back sentence by sentence.
All logic lives in agent.py; chat and call use the same messages.

File map (top to bottom):
  1. Settings
  2. Pages and small APIs   GET /  /health  /api/config  /api/tts
  3. Conversation socket    WebSocket /ws

WebSocket messages
  browser -> server                                server -> browser
  {"type":"start","lang":"Hindi"}                  {"type":"sentence","text":"...","say":"..."}   (many)
  {"type":"text","text":"caller words"}            {"type":"end","ended":false,"saved":false}     (turn over)
  {"type":"unheard"}   speech heard, not understood {"type":"error","message":"..."}
  {"type":"silence"}   caller said nothing
  {"type":"end"}       caller hung up
Every browser message gets an answer made of sentence events followed by one end event.
"""
import asyncio
import io
import logging
import os
import threading

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import agent

log = logging.getLogger("ccms.web")

# ================================================================= 1. SETTINGS
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "static", "index.html")

SILENCE_WAIT_SECONDS = 15   # browser: how long to wait for the caller to start speaking
MAX_TEXT_CHARS = 1000       # longest message accepted from a caller
MAX_TURNS = 200             # most messages in one conversation (protects your Gemini quota)
TTS_MAX_CHARS = 400         # longest text for the optional server voice (/api/tts)

# One-tap questions on the welcome screen. Edit to match knowledge_base.md.
# A language without its own list uses the English one (the agent still answers in that language).
QUICK_QUESTIONS = {
    "English (India)": [
        "What is CCMS?",
        "How do I add a customer?",
        "How do I raise a support ticket?",
        "I forgot my password",
        "I want a callback from the support team",
    ],
    "Hindi": [
        "CCMS क्या है?",
        "कस्टमर कैसे जोड़ें?",
        "सपोर्ट टिकट कैसे बनाएं?",
        "मैं अपना पासवर्ड भूल गया",
        "मुझे सपोर्ट टीम से कॉलबैक चाहिए",
    ],
}

app = FastAPI(title="CCMS Support Agent")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ======================================================== 2. PAGES AND SMALL APIS
@app.get("/")
def home():
    """The chat / call page."""
    return FileResponse(INDEX_FILE)


@app.get("/health")
def health():
    """For the hosting platform's health check."""
    return {"ok": True, "configured": agent.is_configured()}


@app.get("/api/config")
def config():
    """Everything the page needs to draw itself."""
    return {
        "problem": agent.config_problem(),          # None, or a message to show instead of the app
        "default_language": agent.DEFAULT_LANGUAGE,
        "languages": agent.client_languages(),      # label + speech-recognition code + voice code
        "quick_questions": QUICK_QUESTIONS,
        "silence_wait": SILENCE_WAIT_SECONDS,
    }


@app.get("/api/tts")
async def server_voice(text: str = Query(..., max_length=TTS_MAX_CHARS),
                       lang: str = Query("en", max_length=8)):
    """OPTIONAL fallback voice (Google gTTS), used only when the visitor's browser has no voice for
    the chosen language. Needs `pip install gTTS`."""
    if lang not in {cfg["tts"] for cfg in agent.LANGUAGES.values()}:
        raise HTTPException(400, "unsupported language")
    try:
        from gtts import gTTS
    except ImportError:
        raise HTTPException(501, "gTTS is not installed on the server")

    def make() -> bytes:
        buf = io.BytesIO()
        gTTS(text=text, lang=lang).write_to_fp(buf)
        return buf.getvalue()

    try:
        audio = await asyncio.to_thread(make)
    except Exception:
        log.exception("gTTS failed")
        raise HTTPException(502, "voice service unavailable")
    return Response(audio, media_type="audio/mpeg")


# ===================================================== 3. CONVERSATION SOCKET
async def run_turn(ws: WebSocket, session: "agent.SupportAgent", events) -> None:
    """Run one turn and forward its events to the browser as they happen.

    `events` is a normal (blocking) generator from agent.py. It runs in a worker thread so the
    server stays free for other callers, and every sentence is sent the moment Gemini finishes it."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def pump() -> None:                          # runs in the worker thread
        try:
            for event in events:
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception:
            log.exception("Turn failed")
            loop.call_soon_threadsafe(queue.put_nowait, ("error", "Something went wrong, please try again."))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()
    while True:
        event = await queue.get()
        if event is None:
            return
        kind, value = event
        if kind == "sentence":
            await ws.send_json({"type": "sentence", "text": value,
                                "say": agent.speech_text(value, session.language)})
        elif kind == "end":
            await ws.send_json({"type": "end", **value})
        else:
            await ws.send_json({"type": "error", "message": value})


@app.websocket("/ws")
async def conversation(ws: WebSocket):
    """One WebSocket = one conversation (one SupportAgent)."""
    await ws.accept()
    session = None
    turns = 0
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type") if isinstance(msg, dict) else None

            if kind == "start":                  # a new call or chat
                try:
                    session = await asyncio.to_thread(agent.SupportAgent, str(msg.get("lang", "")))
                except Exception as e:           # bad key, missing knowledge base ...
                    log.exception("Could not start the agent")
                    await ws.send_json({"type": "error", "message": f"Could not start: {e}"})
                    continue
                turns = 0
                await run_turn(ws, session, agent.fixed_events(session.greeting, False))

            elif kind == "end":                  # caller hung up
                break

            elif kind in ("text", "unheard", "silence"):
                if session is None:
                    await ws.send_json({"type": "error", "message": "Conversation not started."})
                    continue
                turns += 1
                if turns > MAX_TURNS:
                    await ws.send_json({"type": "error", "message": "Conversation limit reached."})
                    break
                if kind == "text":
                    text = str(msg.get("text", "")).strip()[:MAX_TEXT_CHARS]
                    events = session.hear_stream(text)      # "" counts as "not understood"
                elif kind == "unheard":
                    events = session.hear_stream("")
                else:
                    events = session.silence_stream()
                await run_turn(ws, session, events)
            # anything else is ignored
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Socket error")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":                       # python app.py  (same as the uvicorn command)
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))