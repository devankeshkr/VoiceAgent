"""UI only. All logic lives in agent.py, all styling lives in styles.css.

Run:  streamlit run app.py
- Header (top right): the language icon.
- Bottom centre: the green Call and blue Chat buttons.
- Call: the agent speaks first (its text types out in step with its voice), then
  listens (your words appear live). If you say nothing it asks "Are you still
  there?", and if there is still no answer it ends the call.
- Chat: a normal text chatbot.
Runs on your own computer (uses this PC's microphone and speakers).

File map (top to bottom):
  1. Settings        4. Page layout (header, chat panel, bottom bar)
  2. State           5. Chat mode  (typed messages)
  3. Draw helpers    6. Call mode  (voice loop)
"""
import html
import os
import time

import streamlit as st

import agent  # backend: SupportAgent, speak_prepare, listen_live, ...
   
st.set_page_config(page_title="Ankesh Software Solutions | Support",
                   page_icon="🎧", layout="centered")

# ================================================================= 1. SETTINGS
HEADSET = ('<svg width="{s}" height="{s}" viewBox="0 0 24 24" fill="#ffffff"><path d="M12 1c-4.97 0-9 4.03-9 9v7c0 '
           '1.66 1.34 3 3 3h3v-8H5v-2c0-3.87 3.13-7 7-7s7 3.13 7 7v2h-4v8h4v1h-7v2h6c1.66 0 3-1.34 3-3V10c0-4.97-4.03-9-9-9z"/></svg>')

# Layout: the header and the footer run the full width of the window. The chat panel and the
# footer controls share one centred column (same left and right edges), with space on both sides.
GUT = "max(12px, calc((100% - 820px) / 2))"   # side space that keeps the column at most 820 px wide
HEADER_H = 70         # header height on a computer screen (px)  <- change this to make the header taller or shorter
HEADER_H_MOBILE = 54  # header height on a phone (px)
TOP_H = HEADER_H + 10         # where the chat panel starts
TOP_H_MOBILE = HEADER_H_MOBILE

BAR_HEIGHT = {"idle": 126, "call": 126, "chat": 104}   # bottom bar height per mode (px)
SECONDS_PER_WORD = 0.30     # voice pace used to time the typing effect
VOICE_START_DELAY = 0.1     # small delay between 'audio ready' and the voice being heard
GLIDE_CHARS = 3             # characters added per typing step

# Keeps the chat window scrolled to the newest message (unless you scroll up to read)
SCROLL_JS = """
<script>
(function () {
  var P = window.parent;
  if (P.__chatScrollInstalled) return;
  P.__chatScrollInstalled = true;
  var code = '(' + function () {
    var stick = true, cached = null;
    function scroller() {
      if (cached && document.contains(cached) && cached.scrollHeight > cached.clientHeight + 2) return cached;
      var box = document.querySelector('.st-key-chatbox');
      if (!box) return null;
      var list = [box].concat(Array.prototype.slice.call(box.querySelectorAll('div')));
      for (var i = 0; i < list.length; i++) {
        var o = getComputedStyle(list[i]).overflowY;
        if ((o === 'auto' || o === 'scroll') && list[i].scrollHeight > list[i].clientHeight + 2) { cached = list[i]; return cached; }
      }
      return box;
    }
    document.addEventListener('scroll', function (e) {
      var el = e.target;
      if (el && el.nodeType === 1 && el.closest && el.closest('.st-key-chatbox')) {
        stick = (el.scrollHeight - el.scrollTop - el.clientHeight) < 100;
      }
    }, true);
    var pending = false;
    new MutationObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () {
        pending = false;
        var el = scroller();
        if (el && stick) el.scrollTop = el.scrollHeight;
      });
    }).observe(document.body, {childList: true, subtree: true, characterData: true});
  }.toString() + ')();';
  var s = P.document.createElement('script');
  s.textContent = code;
  P.document.head.appendChild(s);
})();
</script>
"""


def load_css() -> str:
    """Read styles.css and fill in the sizes. (Mobile names first: "TOPH" is part of "MTOPH".)"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.css")
    with open(path, encoding="utf-8") as f:
        css = f.read()
    return (css.replace("MTOPH", str(TOP_H_MOBILE)).replace("MHDRH", str(HEADER_H_MOBILE))
               .replace("TOPH", str(TOP_H)).replace("HDRH", str(HEADER_H)).replace("GUT", GUT))


# ==================================================================== 2. STATE
ss = st.session_state
ss.setdefault("mode", "idle")          # idle | call | chat
ss.setdefault("lang", agent.DEFAULT_LANGUAGE)
ss.setdefault("messages", [])          # [{"role": "user"|"assistant"|"note", "content": ..., "ts": ...}]
ss.setdefault("say", None)             # text the agent should speak next (call mode)
ss.setdefault("hangup", False)         # True when the text in `say` is the final goodbye
ss.setdefault("show_lang", False)      # is the language menu open?


def stamp() -> str:
    return time.strftime("%I:%M %p").lstrip("0")


def add_message(role: str, content: str) -> None:
    ss.messages.append({"role": role, "content": content, "ts": stamp()})


def begin(mode: str) -> None:
    """Call / Chat button clicked: start a new conversation."""
    ss.agent = agent.SupportAgent(ss.lang)
    greeting = ss.agent.greeting
    ss.mode, ss.hangup, ss.messages, ss.show_lang = mode, False, [], False
    if mode == "call":
        ss.say = greeting            # the agent speaks first
        if ss.lang != agent.DEFAULT_LANGUAGE:
            ss.toast = (f"{ss.lang} is spoken with the {ss.lang} voice installed on this PC "
                        "(Windows Settings > Time & language > Speech).")
    else:
        ss.say = None
        add_message("assistant", greeting)


def finish(note: str) -> None:
    """End the call / chat and go back to the start screen."""
    ss.mode, ss.say = "idle", None
    add_message("note", note)


def toggle_lang_menu() -> None:
    ss.show_lang = not ss.show_lang


def pick_lang() -> None:
    ss.lang = ss.lang_choice
    ss.show_lang = False


if not agent.is_configured():
    st.error("GEMINI_API_KEY is missing. Add it to your .env file and restart the app.")
    st.stop()


# ============================================================= 3. DRAW HELPERS
# history_ph, live_ph and status are placeholders created in section 4.
def set_status(kind: str, text: str) -> None:
    """Status pill in the header: kind = speak | listen | think."""
    status.markdown(f'<div class="pill {kind}"><span class="pdot"></span>{text}</div>', unsafe_allow_html=True)


def bubble(role: str, text: str, ts: str = "", dots: bool = False) -> str:
    """HTML for one chat bubble (role = assistant | user | note)."""
    if role == "note":
        return f'<div class="note"><span>{html.escape(text)}</span></div>'
    body = ('<span class="dots"><i></i><i></i><i></i></span>' if dots
            else html.escape(text).replace("\n", "<br>"))
    foot = f'<div class="ts">{ts}</div>' if ts else ""
    if role == "assistant":
        return (f'<div class="row a"><div class="av">{HEADSET.format(s=17)}</div>'
                f'<div class="bub">{body}{foot}</div></div>')
    return f'<div class="row u"><div class="bub">{body}{foot}</div></div>'


def draw_history() -> None:
    """Draw all finished messages (or the welcome screen when there are none)."""
    if not ss.messages and ss.mode == "idle":
        history_ph.markdown(
            '<div class="hero"><div class="ic">' + HEADSET.format(s=34) + '</div>'
            '<div class="t">How can we help you today?</div>'
            '<div class="s">Tap Call to talk or Chat to type. Change the language with the globe at the top.</div></div>',
            unsafe_allow_html=True)
        return
    history_ph.markdown("".join(bubble(m["role"], m["content"], m.get("ts", "")) for m in ss.messages),
                        unsafe_allow_html=True)


def show_live(role: str, text: str) -> None:
    """The bubble that is still being typed / spoken (empty text removes it)."""
    if text:
        live_ph.markdown(bubble(role, text), unsafe_allow_html=True)
    else:
        live_ph.empty()


def show_dots() -> None:
    live_ph.markdown(bubble("assistant", "", dots=True), unsafe_allow_html=True)


def toast_if_saved(saved_before: int) -> None:
    if agent.count_saved_requests() > saved_before:
        st.toast("Your request has been recorded.", icon="✅")


def type_reply(text: str, speak: bool, prep=None) -> None:
    """Show the agent's reply with a typing effect (in step with the voice when speaking).
    `prep` is a voice that was warmed up earlier, so speaking starts at once."""
    proc = None
    if speak:
        if prep is not None and prep.poll() is None:
            proc = prep
            agent.speak_send(proc, text)
        else:
            if prep is not None:
                agent.speak_cancel(prep)
            proc = agent.speak_start(text, ss.lang)

    est = max(1.0, len(text.split()) * SECONDS_PER_WORD)  # estimated speaking time
    if proc is not None:
        show_dots()                                  # the voice is fetching its audio: show "typing..."
        length = agent.speak_wait_start(proc)        # returns when the audio starts playing
        if length:
            est = max(0.5, length)                   # real length of the audio
    t0, shown = time.time(), 0
    try:
        while shown < len(text) or (proc is not None and proc.poll() is None):
            if proc is None or proc.poll() is not None:
                target = len(text)                         # voice finished (or no voice): show the rest
            else:
                progress = (time.time() - t0 - VOICE_START_DELAY) / est
                target = int(len(text) * min(1.0, max(0.0, progress)))
            if shown < target:
                shown = min(target, shown + GLIDE_CHARS)
                show_live("assistant", text[:shown])
            time.sleep(0.03)
        if proc is not None:
            agent.speak_finish(proc)
    finally:
        if proc is not None:
            agent.speak_stop(proc)   # interrupted (End call): silence the voice
        add_message("assistant", text)
    live_ph.empty()
    draw_history()


# ============================================================== 4. PAGE LAYOUT
mode = ss.mode
bar = BAR_HEIGHT[mode]
dynamic_css = (f".st-key-bottombar {{height: {bar}px;}} "
               f".st-key-chatbox {{height: calc(100vh - {TOP_H + bar + 12}px); height: calc(100dvh - {TOP_H + bar + 12}px);}} "
               f"@media (max-width: 640px) {{.st-key-chatbox {{height: calc(100vh - {TOP_H_MOBILE + bar + 10}px); "
               f"height: calc(100dvh - {TOP_H_MOBILE + bar + 10}px);}}}}")
st.markdown(f"<style>{load_css()}{dynamic_css}</style>", unsafe_allow_html=True)

# ---- header: logo + name | status pill | online badge | language icon
badge = {"idle": '<span class="dot"></span>Online',
         "call": '<span class="dot live"></span>On call',
         "chat": '<span class="dot"></span>Chatting'}[mode]
with st.container(key="topbar"):
    with st.container(key="hdrwrap"):
        c_name, c_status, c_badge, c_lang = st.columns([1, 0.3, 0.2, 0.1], vertical_alignment="center")
        with c_name:
            st.markdown(
                '<div class="hdrl"><div class="logo">' + HEADSET.format(s=30) + '</div><div class="txt">'
                '<div class="name">Ankesh Software Solutions</div>'
                f'<div class="sub">CCMS Support Assistant &middot; {html.escape(agent.LANGUAGES[ss.lang]["label"])}</div>'
                '</div></div>', unsafe_allow_html=True)
        with c_status:
            status = st.empty()
        with c_badge:
            st.markdown(f'<div class="badge">{badge}</div>', unsafe_allow_html=True)
        with c_lang:
            with st.container(key="langbtn"):
                st.button("Language", icon=":material/language:", key="lang_btn", on_click=toggle_lang_menu,
                          disabled=mode != "idle", help="Change language")

# ---- language menu
if ss.show_lang and mode == "idle":
    with st.container(key="langpanel"):
        options = list(agent.LANGUAGES)
        st.radio("Language", options, index=options.index(ss.lang), key="lang_choice", on_change=pick_lang,
                 format_func=lambda k: agent.LANGUAGES[k]["label"])

# ---- auto-scroll script
try:
    if hasattr(st, "iframe"):                 # newer Streamlit
        st.iframe(SCROLL_JS, height=1)
    else:                                     # older Streamlit
        import streamlit.components.v1 as components
        components.html(SCROLL_JS, height=0)
except Exception:
    pass  # scrolling by hand still works

# ---- chat panel
with st.container(key="chatbox"):
    with st.container():  # single child
        history_ph = st.empty()   # finished messages
        live_ph = st.empty()      # the message being typed / spoken / heard right now

# ---- bottom bar: Call + Chat buttons, or End call, or the text box + End chat
typed = None
with st.container(key="bottombar"):
    if mode == "idle":
        with st.container(key="idlebar"):
            c_call, c_chat = st.columns(2)
            with c_call:
                st.button("Call", icon=":material/call:", key="call_btn",
                          on_click=begin, args=("call",), help="Call the assistant")
            with c_chat:
                st.button("Chat", icon=":material/chat:", key="chat_btn",
                          on_click=begin, args=("chat",), help="Chat with the assistant")
    elif mode == "call":
        with st.container(key="callbar"):
            st.button("End call", icon=":material/call_end:", key="end_call_btn",
                      on_click=finish, args=("Call ended",), help="End call")
    else:
        c_text, c_end = st.columns([8, 1], vertical_alignment="center")
        with c_text:
            typed = st.chat_input("Type your message...")
        with c_end:
            st.button("End chat", icon=":material/close:", key="end_chat_btn",
                      on_click=finish, args=("Chat ended",), help="End chat")

if ss.get("toast"):
    st.toast(ss.pop("toast"), icon="🔊")

draw_history()


# ============================================================== 5. CHAT MODE
def handle_typed_message(text: str) -> None:
    add_message("user", text)
    draw_history()
    show_dots()
    try:
        saved_before = agent.count_saved_requests()
        reply, ended = ss.agent.hear(text)
    except Exception as e:
        live_ph.empty()
        add_message("note", f"Something went wrong, please try again. ({e})")
        draw_history()
        return
    live_ph.empty()
    type_reply(reply, speak=False)
    toast_if_saved(saved_before)
    if ended:
        finish("Chat ended")
        st.rerun()


if mode == "chat" and typed:
    handle_typed_message(typed)


# ============================================================== 6. CALL MODE
def listen_and_reply():
    """Listen to the caller and work out the agent's next words. Returns (reply, ended)."""
    set_status("listen", "Listening... please speak")
    heard, spoke = agent.listen_live(
        on_partial=lambda t: show_live("user", t), language=ss.lang,
        tick=lambda: set_status("listen", "Listening... please speak"))
    live_ph.empty()

    if heard:                                  # the caller said something
        add_message("user", heard)
        draw_history()
        set_status("think", "Thinking...")
        show_dots()
        saved_before = agent.count_saved_requests()
        reply, ended = ss.agent.hear(heard)
        live_ph.empty()
        toast_if_saved(saved_before)
        return reply, ended
    if spoke:                                  # speech was heard but not understood
        return ss.agent.hear("")
    return ss.agent.silence()                  # silence: "Are you still there?", then end the call


def run_call() -> None:
    """Loop: agent speaks -> caller speaks -> agent replies ... until the call ends."""
    prep = agent.speak_prepare(ss.lang)          # warm up the voice for the greeting
    try:
        while ss.mode == "call":
            if ss.say:                           # 1) the agent speaks
                set_status("speak", "Agent is speaking...")
                text, ss.say = ss.say, None
                type_reply(text, speak=True, prep=prep)
                prep = None
                if ss.hangup:                    # the final goodbye has been spoken
                    finish("Call ended")
                    break

            prep = agent.speak_prepare(ss.lang)  # 2) warms up while we listen and think
            ss.say, ss.hangup = listen_and_reply()
    except Exception as e:  # mic / speaker / AI errors end the call cleanly
        finish(f"Call stopped: {e}")
    finally:
        if prep is not None:
            agent.speak_cancel(prep)
    status.empty()
    live_ph.empty()
    if ss.mode != "call":
        st.rerun()


if mode == "call":
    run_call()