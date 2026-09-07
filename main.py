#-----------------Libraries---------------------
import cv2
import mediapipe as mp
import numpy as np
import pygame
import serial
import serial.tools.list_ports          # ← NEW: for auto COM-port detection
import time
import threading
import os
from AI_Con import manual_text
import tkinter as tk
from tkinter import scrolledtext

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE=True
except ImportError:
    PIL_AVAILABLE=False

# ---------------- Display Sizing ----------------
screen_probe=tk.Tk()
screen_probe.withdraw()
DISPLAY_WIDTH=screen_probe.winfo_screenwidth()
DISPLAY_HEIGHT=screen_probe.winfo_screenheight()
screen_probe.destroy()

# ---------------- UI Theme ----------------
BG_COLOR=(19,11,1)
PANEL_COLOR=(30,23,16)
CARD_COLOR=(42,32,24)
CARD_ACTIVE=(70,53,34)
ACCENT_COLOR=(191,212,45)
TEXT_PRIMARY=(236,241,245)
TEXT_MUTED=(164,178,191)
SUCCESS_COLOR=(114,197,96)
ALERT_COLOR=(91,175,210)
DIVIDER_COLOR=(66,52,39)
ICON_TILE=(35,30,25)
ICON_BORDER=(86,76,58)
ICON_PRIMARY=(210,224,142)
ICON_SECONDARY=(232,238,242)
ICON_ALERT=(238,195,118)
ICON_DANGER=(221,110,110)
ICON_SURFACE=(19,11,1)
ICON_PANEL=(24,34,52)
ICON_RING=(72,96,132)

emoji_labels={
    "bell":"Call Bell",
    "bulb":"Light",
    "fan":"Fan",
    "music":"Music",
    "chat":"Guide",
    "toilet":"Washroom",
    "water":"Water",
    "food":"Meal",
    "question":"Need Help",
    "yes":"Yes",
    "no":"No",
    "wave":"Greeting",
    "happy":"Comfortable",
    "sad":"Sad",
    "angry":"Upset",
    "cry":"Pain",
    "laugh":"Happy",
    "heart":"Care"
}

emoji_glyphs={
    "bell":"🔔",
    "bulb":"💡",
    "fan":"🌀",
    "music":"🎵",
    "chat":"💬",
    "toilet":"🚽",
    "water":"💧",
    "food":"🍽️",
    "question":"❓",
    "yes":"✅",
    "no":"❌",
    "wave":"👋",
    "happy":"🙂",
    "sad":"☹️",
    "angry":"😠",
    "cry":"😭",
    "laugh":"😄",
    "heart":"❤️"
}

# ================================================================
# FIX 1 — Arduino Setup: auto-detect port + proper delay + logging
# ================================================================

ARDUINO_BAUD = 9600
ARDUINO_TIMEOUT = 1
ARDUINO_STABILIZE_DELAY = 2.0   # seconds to wait after port opens (Arduino resets on connect)

def find_arduino_port():
    """
    Scan all available COM ports and return the first one that looks like
    an Arduino (USB Serial / CH340 / FTDI / ATmega).  Falls back to COM4
    if nothing is recognised so existing wiring still works.
    """
    keywords = ("arduino", "ch340", "ch341", "ftdi", "usb serial", "atm")
    ports = serial.tools.list_ports.comports()

    for port in ports:
        desc = (port.description or "").lower()
        mfr  = (port.manufacturer or "").lower()
        if any(kw in desc or kw in mfr for kw in keywords):
            print(f"[Arduino] Auto-detected on {port.device}  ({port.description})")
            return port.device

    # Nothing recognised — list what IS available so the user can debug
    if ports:
        available = ", ".join(p.device for p in ports)
        print(f"[Arduino] No Arduino signature found. Available ports: {available}")
        print("[Arduino] Falling back to COM4. Change ARDUINO_FALLBACK_PORT if needed.")
    else:
        print("[Arduino] No serial ports found at all. Is the USB cable plugged in?")

    return "COM4"   # ← change this constant if your fallback differs

ARDUINO_FALLBACK_PORT = "COM4"


def connect_arduino():
    """
    Try to open the Arduino serial port.
    Returns a Serial object on success, or None on failure.
    Waits ARDUINO_STABILIZE_DELAY seconds so the Arduino has time to
    finish its reset before the first command is sent.
    """
    port = find_arduino_port()
    try:
        ser = serial.Serial(port, ARDUINO_BAUD, timeout=ARDUINO_TIMEOUT)
        # ── FIX 2: wait for the Arduino bootloader to finish resetting ──
        print(f"[Arduino] Port {port} opened. Waiting {ARDUINO_STABILIZE_DELAY}s for reset…")
        time.sleep(ARDUINO_STABILIZE_DELAY)
        print("[Arduino] Ready.")
        return ser
    except serial.SerialException as exc:
        # ── FIX 3: print the REAL error, not just a generic message ──
        print(f"[Arduino] Connection FAILED on {port}: {exc}")
        print("[Arduino] Dashboard will run without hardware control.")
        return None
    except Exception as exc:
        print(f"[Arduino] Unexpected error: {exc}")
        return None


arduino = connect_arduino()


# ================================================================
# FIX 4 — Safe write helper + auto-reconnect on failure
# ================================================================

_arduino_lock = threading.Lock()   # prevents two threads writing at the same time

def arduino_send(cmd: bytes) -> bool:
    """
    Thread-safe Arduino write with automatic one-shot reconnect.
    Returns True if the command was sent, False otherwise.
    """
    global arduino

    with _arduino_lock:
        if arduino is None:
            # Try to reconnect once before giving up
            arduino = connect_arduino()

        if arduino is None:
            print(f"[Arduino] Cannot send {cmd!r}: not connected.")
            return False

        try:
            if not arduino.isOpen():
                arduino.open()
                time.sleep(ARDUINO_STABILIZE_DELAY)
            arduino.write(cmd)
            return True
        except (serial.SerialException, OSError) as exc:
            print(f"[Arduino] Write error ({cmd!r}): {exc}. Attempting reconnect…")
            try:
                arduino.close()
            except Exception:
                pass
            arduino = connect_arduino()   # one reconnect attempt
            if arduino:
                try:
                    arduino.write(cmd)
                    return True
                except Exception as exc2:
                    print(f"[Arduino] Reconnect write also failed: {exc2}")
            return False


# ---------------- Music Setup ----------------
pygame.mixer.init()
music_files=["music1.mp3","music2.mp3","music3.mp3","music4.mp3"]
music_toggle=False
current_track=0


# ---------------- Emoji Names ----------------
emoji_names=[
"bell","bulb","fan","music","chat","toilet","water","food","question",
"yes","no","wave","happy","sad","angry","cry","laugh","heart"
]

BELL_INDEX=0
LED_INDEX=1
FAN_INDEX=2
MUSIC_INDEX=3
CHAT_INDEX=4

# ---------------- Arduino Command Mapping ----------------
LIGHT_ON_CMD  = b'L'
LIGHT_OFF_CMD = b'H'
FAN_ON_CMD    = b'G'
FAN_OFF_CMD   = b'F'
BELL_START    = b'B'
# FIX 5: use explicit byte value for bell-stop so it is not confused with ASCII '0'
BELL_STOP     = b'0'   # keep as '0' — just documented clearly now


# ---------------- Manual Window State ----------------
manual_window=None
manual_close_event=threading.Event()
manual_window_open=False


# ---------------- Safe Image Loader ----------------
def load_image(path,size):
    img=cv2.imread(path)
    if img is None:
        print("Missing:",path)
        img=np.ones((size,size,3),dtype=np.uint8)*200
    else:
        img=cv2.resize(img,(size,size))
    return img


def create_icon_canvas(size):
    canvas=np.full((size,size,3),ICON_SURFACE,dtype=np.uint8)
    inset=max(3,size//26)
    cv2.rectangle(canvas,(inset,inset),(size-inset-1,size-inset-1),ICON_PANEL,-1,lineType=cv2.LINE_AA)
    cv2.rectangle(canvas,(inset,inset),(size-inset-1,size-inset-1),ICON_RING,1,lineType=cv2.LINE_AA)
    center=(size//2,size//2)
    cv2.circle(canvas,center,int(size*0.32),(56,68,90),-1,lineType=cv2.LINE_AA)
    cv2.circle(canvas,(center[0]-size//10,center[1]-size//10),int(size*0.12),(76,90,118),-1,lineType=cv2.LINE_AA)
    return canvas


def draw_gloss_badge(canvas,center,radius,fill_color,ring_color):
    cv2.circle(canvas,center,radius,fill_color,-1,lineType=cv2.LINE_AA)
    cv2.circle(canvas,center,radius,ring_color,max(2,radius//7),lineType=cv2.LINE_AA)
    highlight_center=(center[0]-radius//3,center[1]-radius//3)
    cv2.circle(canvas,highlight_center,max(4,radius//4),(240,244,248),-1,lineType=cv2.LINE_AA)
    cv2.circle(canvas,highlight_center,max(2,radius//6),fill_color,-1,lineType=cv2.LINE_AA)


def get_icon_palette(name):
    palettes={
        "bell":((72,126,255),(206,224,255),(255,255,255)),
        "bulb":((104,214,104),(220,255,214),(255,255,255)),
        "fan":((112,188,255),(220,238,255),(255,255,255)),
        "music":((104,224,208),(216,255,248),(255,255,255)),
        "chat":((114,214,255),(220,246,255),(255,255,255)),
        "toilet":((148,208,255),(230,244,255),(255,255,255)),
        "water":((102,196,255),(218,240,255),(255,255,255)),
        "food":((255,166,116),(255,230,206),(255,255,255)),
        "question":((142,180,255),(226,236,255),(255,255,255)),
        "yes":((104,220,144),(220,255,230),(255,255,255)),
        "no":((255,118,136),(255,222,228),(255,255,255)),
        "wave":((255,192,126),(255,236,214),(255,255,255)),
        "happy":((255,208,98),(255,240,196),(255,255,255)),
        "sad":((132,184,255),(228,238,255),(255,255,255)),
        "angry":((255,144,108),(255,224,208),(255,255,255)),
        "cry":((118,200,255),(222,242,255),(255,255,255)),
        "laugh":((255,214,110),(255,242,198),(255,255,255)),
        "heart":((255,110,162),(255,220,234),(255,255,255))
    }
    return palettes.get(name,((118,160,214),(226,236,248),(255,255,255)))


def draw_face_icon(canvas,badge_fill,badge_ring,mouth="smile",tear=False,eyebrows=False,laugh=False):
    size=canvas.shape[0]
    center=(size//2,size//2)
    radius=size//3
    thickness=max(2,size//20)
    cv2.circle(canvas,center,radius,badge_fill,-1,lineType=cv2.LINE_AA)
    cv2.circle(canvas,center,radius,badge_ring,thickness,lineType=cv2.LINE_AA)
    eye_y=center[1]-radius//3
    eye_dx=radius//2
    if laugh:
        cv2.ellipse(canvas,(center[0]-eye_dx,eye_y),(radius//5,radius//8),0,200,340,ICON_SECONDARY,thickness,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(center[0]+eye_dx,eye_y),(radius//5,radius//8),0,200,340,ICON_SECONDARY,thickness,lineType=cv2.LINE_AA)
    else:
        cv2.circle(canvas,(center[0]-eye_dx,eye_y),max(1,size//26),ICON_SECONDARY,-1,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(center[0]+eye_dx,eye_y),max(1,size//26),ICON_SECONDARY,-1,lineType=cv2.LINE_AA)
    if eyebrows:
        brow_y=eye_y-radius//5
        cv2.line(canvas,(center[0]-eye_dx-radius//6,brow_y),(center[0]-eye_dx+radius//6,brow_y+radius//8),ICON_DANGER,thickness,lineType=cv2.LINE_AA)
        cv2.line(canvas,(center[0]+eye_dx-radius//6,brow_y+radius//8),(center[0]+eye_dx+radius//6,brow_y),ICON_DANGER,thickness,lineType=cv2.LINE_AA)
    mouth_center=(center[0],center[1]+radius//4)
    mouth_axes=(radius//2,radius//3)
    if mouth=="smile":
        cv2.ellipse(canvas,mouth_center,mouth_axes,0,20,160,ICON_SECONDARY,thickness,lineType=cv2.LINE_AA)
    elif mouth=="sad":
        cv2.ellipse(canvas,(mouth_center[0],mouth_center[1]+radius//3),(mouth_axes[0],mouth_axes[1]//2),0,200,340,ICON_SECONDARY,thickness,lineType=cv2.LINE_AA)
    elif mouth=="open":
        cv2.ellipse(canvas,mouth_center,(mouth_axes[0]//2,mouth_axes[1]//2),0,0,360,ICON_SECONDARY,thickness,lineType=cv2.LINE_AA)
    if tear:
        left_tear_x=center[0]-eye_dx
        right_tear_x=center[0]+eye_dx
        tear_top=eye_y+radius//10
        tear_bottom=tear_top+radius//2
        cv2.line(canvas,(left_tear_x,tear_top),(left_tear_x,tear_bottom),ALERT_COLOR,thickness,lineType=cv2.LINE_AA)
        cv2.line(canvas,(right_tear_x,tear_top),(right_tear_x,tear_bottom),ALERT_COLOR,thickness,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(left_tear_x,tear_bottom),max(1,size//28),ALERT_COLOR,-1,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(right_tear_x,tear_bottom),max(1,size//28),ALERT_COLOR,-1,lineType=cv2.LINE_AA)


def create_professional_icon(name,size):
    canvas=create_icon_canvas(size)
    t=max(2,size//18)
    c=size//2
    r=size//3
    badge_fill,badge_ring,symbol_color=get_icon_palette(name)
    badge_radius=int(size*0.3)
    draw_gloss_badge(canvas,(c,c),badge_radius,badge_fill,badge_ring)

    if name=="bell":
        bell_color=symbol_color
        shine_color=badge_ring
        body=np.array([[c-r//2,c+r//4],[c-r//2+6,c-r//8],[c-r//4,c-r//2],[c+r//4,c-r//2],[c+r//2-6,c-r//8],[c+r//2,c+r//4]],np.int32)
        cv2.fillConvexPoly(canvas,body,bell_color,lineType=cv2.LINE_AA)
        cv2.polylines(canvas,[body],True,shine_color,t,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c,c+r//4),(r//2,r//6),0,0,180,shine_color,t,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(c,c+r//3+8),max(4,size//16),(255,214,124),-1,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c,c-r//2-4),(c,c-r//2-18),shine_color,t,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(c,c-r//2-20),max(2,size//30),shine_color,-1,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c-r//2-8,c-r//8),(r//5,r//3),0,290,65,ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c+r//2+8,c-r//8),(r//5,r//3),0,115,250,ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
    elif name=="bulb":
        bulb_color=symbol_color
        cv2.ellipse(canvas,(c,c-4),(r//2,r//2),0,0,360,bulb_color,-1,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c,c-4),(r//2,r//2),0,0,360,ICON_SECONDARY,t-1,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//4,c+r//3),(c+r//4,c+r//3),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//5,c+r//3+8),(c+r//5,c+r//3+8),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        for ray in [(-20,-22),(0,-30),(20,-22)]:
            cv2.line(canvas,(c+ray[0]//2,c+ray[1]//2),(c+ray[0],c+ray[1]),ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
    elif name=="fan":
        cv2.circle(canvas,(c,c),max(3,size//18),ICON_SECONDARY,-1,lineType=cv2.LINE_AA)
        for angle in [0,120,240]:
            rot=np.deg2rad(angle)
            x2=int(c+np.cos(rot)*r//2)
            y2=int(c+np.sin(rot)*r//2)
            cv2.ellipse(canvas,(x2,y2),(r//3,r//5),angle,20,220,symbol_color,-1,lineType=cv2.LINE_AA)
            cv2.ellipse(canvas,(x2,y2),(r//3,r//5),angle,20,220,ICON_SECONDARY,max(1,t-2),lineType=cv2.LINE_AA)
    elif name=="music":
        cv2.line(canvas,(c-8,c-r//2),(c-8,c+r//4),symbol_color,t+2,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c+20,c-r//2-6),(c+20,c+r//6),symbol_color,t+2,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-8,c-r//2),(c+20,c-r//2-6),symbol_color,t+2,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(c-18,c+r//4+4),max(4,size//12),ICON_SECONDARY,t+1,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(c+10,c+r//6+10),max(4,size//12),ICON_SECONDARY,t+1,lineType=cv2.LINE_AA)
    elif name=="chat":
        cv2.rectangle(canvas,(c-r//2,c-r//3),(c+r//2,c+r//4),symbol_color,-1,lineType=cv2.LINE_AA)
        cv2.rectangle(canvas,(c-r//2,c-r//3),(c+r//2,c+r//4),ICON_SECONDARY,t-1,lineType=cv2.LINE_AA)
        points=np.array([[c-r//5,c+r//4],[c-r//10,c+r//4],[c-r//4,c+r//2]],np.int32)
        cv2.polylines(canvas,[points],False,ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        for dx in [-14,0,14]:
            cv2.circle(canvas,(c+dx,c-2),max(1,size//28),ICON_PANEL,-1,lineType=cv2.LINE_AA)
    elif name=="toilet":
        seat_y=c+6
        cv2.rectangle(canvas,(c-r//2,c-r//2),(c-r//5,c-r//8),symbol_color,-1,lineType=cv2.LINE_AA)
        cv2.rectangle(canvas,(c-r//2,c-r//2),(c-r//5,c-r//8),ICON_SECONDARY,t-1,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//2,c-r//8),(c-r//2,c+r//2),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//5,c-r//8),(c-r//5,c+r//2),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c+r//10,seat_y),(r//3,r//5),0,180,360,ICON_SECONDARY,t+1,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//6,seat_y),(c+r//4,seat_y),ICON_SECONDARY,t+1,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c+r//4,seat_y),(c+r//3,c+r//4),ICON_SECONDARY,t+1,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//10,c+r//5),(c+r//4,c+r//5),symbol_color,t+1,lineType=cv2.LINE_AA)
    elif name=="water":
        pts=np.array([[c-r//2,c-r//3],[c+r//2,c-r//3],[c+r//3,c+r//2],[c-r//3,c+r//2]],np.int32)
        cv2.polylines(canvas,[pts],True,ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.fillConvexPoly(canvas,pts,symbol_color,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c,c),(r//3,r//8),0,0,180,ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c,c+10),(r//3,r//8),0,0,180,ICON_SECONDARY,t,lineType=cv2.LINE_AA)
    elif name=="food":
        cv2.ellipse(canvas,(c,c+10),(r//2,r//8),0,0,360,ICON_SECONDARY,t+1,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c,c),(r//2,r//3),0,180,360,symbol_color,-1,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c,c),(r//2,r//3),0,180,360,ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(c,c-r//3),max(1,size//30),ICON_SECONDARY,-1,lineType=cv2.LINE_AA)
    elif name=="question":
        cv2.putText(canvas,"?",(c-r//4,c+r//3),cv2.FONT_HERSHEY_COMPLEX,1.4 if size<120 else 2.1,ICON_SECONDARY,t+1,cv2.LINE_AA)
    elif name=="yes":
        cv2.line(canvas,(c-r//2,c+4),(c-r//8,c+r//2),ICON_SECONDARY,t+2,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//8,c+r//2),(c+r//2,c-r//3),ICON_SECONDARY,t+2,lineType=cv2.LINE_AA)
    elif name=="no":
        cv2.line(canvas,(c-r//2,c-r//2),(c+r//2,c+r//2),ICON_SECONDARY,t+2,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c+r//2,c-r//2),(c-r//2,c+r//2),ICON_SECONDARY,t+2,lineType=cv2.LINE_AA)
    elif name=="wave":
        hand_color=symbol_color
        palm=np.array([[c-r//4,c+r//3],[c-r//3,c-r//16],[c-r//4,c-r//2],[c-r//10,c-r//2+4],[c,c-r//2-4],[c+r//8,c-r//2+6],[c+r//4,c-r//3],[c+r//3,c+r//10],[c+r//6,c+r//3]],np.int32)
        cv2.fillConvexPoly(canvas,palm,hand_color,lineType=cv2.LINE_AA)
        cv2.polylines(canvas,[palm],True,ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//4,c-r//16),(c-r//3,c-r//2),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//12,c-r//3),(c-r//14,c-r//2),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c+r//18,c-r//3),(c+r//18,c-r//2),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.line(canvas,(c+r//7,c-r//4),(c+r//5,c-r//3),ICON_SECONDARY,t,lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c+r//3,c-r//10),(r//4,r//4),0,280,80,ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
        cv2.ellipse(canvas,(c+r//3+10,c-r//10-4),(r//3,r//3),0,280,70,ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
    elif name=="happy":
        draw_face_icon(canvas,badge_fill,badge_ring,mouth="smile")
    elif name=="sad":
        draw_face_icon(canvas,badge_fill,badge_ring,mouth="sad")
    elif name=="angry":
        draw_face_icon(canvas,badge_fill,badge_ring,mouth="sad",eyebrows=True)
    elif name=="cry":
        draw_face_icon(canvas,badge_fill,badge_ring,mouth="open",tear=True)
        cv2.line(canvas,(c-r//4,c-r//3),(c-r//12,c-r//4),ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
        cv2.line(canvas,(c+r//12,c-r//4),(c+r//4,c-r//3),ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
        cv2.line(canvas,(c-r//6,c+r//3),(c-r//6,c+r//2),ALERT_COLOR,max(1,t-1),lineType=cv2.LINE_AA)
        cv2.line(canvas,(c+r//6,c+r//3),(c+r//6,c+r//2),ALERT_COLOR,max(1,t-1),lineType=cv2.LINE_AA)
    elif name=="laugh":
        draw_face_icon(canvas,badge_fill,badge_ring,mouth="smile",laugh=True)
    elif name=="heart":
        cv2.circle(canvas,(c-r//5,c-r//8),r//4,symbol_color,-1,lineType=cv2.LINE_AA)
        cv2.circle(canvas,(c+r//5,c-r//8),r//4,symbol_color,-1,lineType=cv2.LINE_AA)
        heart=np.array([[c-r//2+4,c-r//10],[c,c+r//2],[c+r//2-4,c-r//10]],np.int32)
        cv2.fillConvexPoly(canvas,heart,symbol_color,lineType=cv2.LINE_AA)
        cv2.polylines(canvas,[heart],True,ICON_SECONDARY,max(1,t-1),lineType=cv2.LINE_AA)
    else:
        cv2.circle(canvas,(c,c),r//2,ICON_SECONDARY,t,lineType=cv2.LINE_AA)

    return canvas


def render_emoji_icon(name,size):
    if not PIL_AVAILABLE:
        return recenter_icon_content(create_professional_icon(name,size))
    try:
        glyph_map={
            "bell":"\U0001F514","bulb":"\U0001F4A1","fan":"\U0001F300",
            "music":"\U0001F3B5","chat":"\U0001F4AC","toilet":"\U0001F6BD",
            "water":"\U0001F4A7","food":"\U0001F37D","question":"\u2753",
            "yes":"\u2705","no":"\u274C","wave":"\U0001F44B",
            "happy":"\U0001F642","sad":"\u2639","angry":"\U0001F620",
            "cry":"\U0001F62D","laugh":"\U0001F604","heart":"\u2764"
        }
        icon=create_icon_canvas(size)
        img=Image.fromarray(cv2.cvtColor(icon,cv2.COLOR_BGR2RGB))
        draw=ImageDraw.Draw(img)
        emoji_font=ImageFont.truetype(r"C:\Windows\Fonts\seguiemj.ttf",max(34,int(size*0.56)))
        glyph=glyph_map.get(name,"\u2754")
        bbox=draw.textbbox((0,0),glyph,font=emoji_font,embedded_color=True)
        text_w=bbox[2]-bbox[0]
        text_h=bbox[3]-bbox[1]
        text_x=(size-text_w)//2-bbox[0]
        text_y=(size-text_h)//2-bbox[1]-max(2,size//32)
        draw.text((text_x,text_y),glyph,font=emoji_font,embedded_color=True)
        rendered=cv2.cvtColor(np.array(img),cv2.COLOR_RGB2BGR)
        return recenter_icon_content(rendered)
    except Exception:
        return recenter_icon_content(create_professional_icon(name,size))


def recenter_icon_content(icon,name=None):
    gray=cv2.cvtColor(icon,cv2.COLOR_BGR2GRAY)
    mask=gray > 90
    points=cv2.findNonZero(mask.astype(np.uint8))
    if points is None:
        return icon
    x,y,w,h=cv2.boundingRect(points)
    content=icon[y:y+h,x:x+w].copy()
    centered=icon.copy()
    inset=max(3,icon.shape[0]//26)
    centered[inset:icon.shape[0]-inset,inset:icon.shape[1]-inset]=icon[inset:icon.shape[0]-inset,inset:icon.shape[1]-inset]
    inner_w=icon.shape[1]-(inset*2)
    inner_h=icon.shape[0]-(inset*2)
    new_x=inset+max(0,(inner_w-w)//2)
    new_y=inset+max(0,(inner_h-h)//2)
    tile=create_icon_canvas(icon.shape[0])
    centered[inset:icon.shape[0]-inset,inset:icon.shape[1]-inset]=tile[inset:icon.shape[0]-inset,inset:icon.shape[1]-inset]
    centered[new_y:new_y+h,new_x:new_x+w]=content
    return centered


# ---------------- Load Emojis ----------------
emojis=[]
for name in emoji_names:
    emojis.append(render_emoji_icon(name,180))


# ---------------- Load Logo ----------------
logo=None
try:
    logo=load_image("AICON_logo.png",180)
except:
    print("Logo not found")


# ================================================================
# FIX 8 — Webcam Setup: DirectShow backend + real frame validation
# ================================================================

def connect_camera():
    """
    Open the webcam reliably on Windows.

    Strategy (tried in order):
      1. DirectShow backend (CAP_DSHOW) — bypasses MSMF; most reliable on Windows
         even when the Windows Camera app works but OpenCV MSMF cannot grab the device.
      2. Default backend (MSMF / platform default) — fallback if DirectShow is absent.

    For each backend, tries camera indices 0, 1, 2 so the correct device is found
    automatically on laptops that have both a built-in and an external camera.

    Validates the connection by actually reading one test frame — isOpened() alone
    returns True even when no frames can be decoded, which is the silent failure mode
    that causes the black/offline camera panel.
    """
    backends = []

    # DirectShow is Windows-only; guard so the code still runs on Linux/Mac
    if hasattr(cv2, "CAP_DSHOW"):
        backends.append((cv2.CAP_DSHOW, "DirectShow"))

    backends.append((None, "Default"))   # None → let OpenCV pick (MSMF on Windows)

    for backend_flag, backend_name in backends:
        for index in range(3):           # try camera indices 0, 1, 2
            try:
                cap = (cv2.VideoCapture(index, backend_flag)
                       if backend_flag is not None
                       else cv2.VideoCapture(index))

                if not cap.isOpened():
                    cap.release()
                    continue

                # Cameras need several frames to warm up after opening.
                # A single cap.read() almost always returns False on the
                # first call — retry up to 10 times with a short pause.
                frame_ok = False
                for _ in range(10):
                    ret, test_frame = cap.read()
                    if ret and test_frame is not None:
                        frame_ok = True
                        break
                    time.sleep(0.05)   # 50 ms between attempts = up to 0.5 s total

                if frame_ok:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    print(f"[Camera] Connected: index={index}  backend={backend_name}")
                    return cap

                cap.release()

            except Exception as exc:
                print(f"[Camera] index={index} backend={backend_name} error: {exc}")

    print("[Camera] No webcam could be opened. Running in offline / placeholder mode.")
    return None


# ---------------- Webcam Setup ----------------
mp_face_mesh=mp.solutions.face_mesh
face_mesh=mp_face_mesh.FaceMesh(refine_landmarks=True)

cap=connect_camera()
camera_connected = cap is not None

LEFT_EYE=[33,160,158,133,153,144]
RIGHT_EYE=[362,385,387,263,373,380]

cursor_index=0
cooldown=0
both_eye_start=None


# ---------------- States ----------------
bulb_on=False
fan_on=False
bell_active=False

show_selected=False
selected_emoji=None
selected_start=0


# ---------------- Eye Ratio ----------------
def eye_ratio(landmarks,eye_points,w,h):
    pts=[]
    for p in eye_points:
        x=int(landmarks[p].x*w)
        y=int(landmarks[p].y*h)
        pts.append((x,y))
    v1=np.linalg.norm(np.array(pts[1])-np.array(pts[5]))
    v2=np.linalg.norm(np.array(pts[2])-np.array(pts[4]))
    h1=np.linalg.norm(np.array(pts[0])-np.array(pts[3]))
    return (v1+v2)/(2*h1)


# ---------------- Music Thread ----------------
def music_loop():
    global current_track
    while music_toggle:
        pygame.mixer.music.load(music_files[current_track])
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy() and music_toggle:
            pygame.time.wait(100)
        current_track=(current_track+1)%len(music_files)


# ---------------- Manual Guide ----------------
def close_manual_window():
    global manual_window, manual_window_open
    manual_close_event.set()
    if manual_window is not None:
        try:
            manual_window.after(0, manual_window.destroy)
        except Exception:
            pass
    manual_window=None
    manual_window_open=False


def show_manual():
    global manual_window, manual_window_open
    manual_close_event.clear()
    window=tk.Tk()
    manual_window=window
    manual_window_open=True

    def on_close():
        global manual_window, manual_window_open
        manual_window_open=False
        manual_window=None
        manual_close_event.set()
        window.destroy()

    def poll_close():
        if manual_close_event.is_set():
            on_close()
            return
        window.after(150,poll_close)

    window.title("AICon User Guide")
    window.geometry("560x440+780+250")
    window.configure(bg="#010B13")
    window.protocol("WM_DELETE_WINDOW",on_close)
    txt=scrolledtext.ScrolledText(
        window,wrap=tk.WORD,font=("Segoe UI",11),
        bg="#071521",fg="#EAF1F6",insertbackground="#EAF1F6",
        relief=tk.FLAT,padx=16,pady=16
    )
    txt.pack(expand=True,fill="both",padx=12,pady=12)
    txt.insert(tk.END,manual_text)
    txt.configure(state='disabled')
    window.after(150,poll_close)
    window.mainloop()


def draw_panel(screen,top_left,bottom_right,color,thickness=-1):
    x1,y1=top_left
    x2,y2=bottom_right
    cv2.rectangle(screen,(x1,y1),(x2,y2),color,thickness,lineType=cv2.LINE_AA)


def add_text(screen,text,position,scale,color,thickness=1,font=cv2.FONT_HERSHEY_DUPLEX):
    cv2.putText(screen,text,position,font,scale,color,thickness,cv2.LINE_AA)


def draw_status_chip(screen,x,y,w,h,label,value,is_on=False,is_alert=False):
    chip_color=CARD_ACTIVE if is_on else CARD_COLOR
    if is_alert:
        chip_color=(54,44,34) if is_on else CARD_COLOR
    draw_panel(screen,(x,y),(x+w,y+h),chip_color)
    label_scale=0.34 if w < 150 else 0.38
    value_scale=0.46 if w < 150 else 0.52
    label_size=cv2.getTextSize(label,cv2.FONT_HERSHEY_DUPLEX,label_scale,1)[0]
    value_color=SUCCESS_COLOR if is_on else TEXT_PRIMARY
    if is_alert:
        value_color=ACCENT_COLOR if is_on else ALERT_COLOR
    value_size=cv2.getTextSize(value,cv2.FONT_HERSHEY_DUPLEX,value_scale,2)[0]
    label_x=x+12
    value_x=x+w-value_size[0]-12
    add_text(screen,label,(label_x,y+22),label_scale,TEXT_MUTED,1)
    add_text(screen,value,(value_x,y+h-18),value_scale,value_color,2)


def build_background(height,width):
    screen=np.zeros((height,width,3),dtype=np.uint8)
    for row in range(height):
        blend=row/max(height-1,1)
        base=np.array(BG_COLOR,dtype=np.float32)
        lift=np.array((36,26,14),dtype=np.float32)
        screen[row,:]=np.clip(base+(lift*blend*0.34),0,255)
    return screen


def build_camera_placeholder(width,height):
    placeholder=np.full((height,width,3),PANEL_COLOR,dtype=np.uint8)
    cv2.rectangle(placeholder,(0,0),(width-1,height-1),DIVIDER_COLOR,2,lineType=cv2.LINE_AA)
    add_text(placeholder,"Camera Offline",(34,height//2-12),0.86,TEXT_PRIMARY,2)
    add_text(placeholder,"Dashboard remains available without webcam",(34,height//2+24),0.52,TEXT_MUTED,1)
    return placeholder


def clamp(value,minimum,maximum):
    return max(minimum,min(value,maximum))


# ---------------- Display Window ----------------
cv2.namedWindow("AI Assist Screen",cv2.WINDOW_NORMAL)
cv2.setWindowProperty("AI Assist Screen",cv2.WND_PROP_FULLSCREEN,cv2.WINDOW_FULLSCREEN)


# ---------------- Main Loop ----------------
while True:

    # FIX 8b — guard against cap being None (no camera found at startup)
    ret=False
    frame=None
    results=None

    if cap is not None:
        ret,frame=cap.read()

    if ret and frame is not None:
        camera_connected=True
        h,w,_=frame.shape
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        results=face_mesh.process(rgb)
    else:
        camera_connected=False
        frame=build_camera_placeholder(640,480)
        h,w,_=frame.shape

    if cooldown>0:
        cooldown-=1

    if results and results.multi_face_landmarks:

        landmarks=results.multi_face_landmarks[0].landmark
        left_ratio=eye_ratio(landmarks,LEFT_EYE,w,h)
        right_ratio=eye_ratio(landmarks,RIGHT_EYE,w,h)

        # LEFT BLINK → move right
        if left_ratio<0.18 and right_ratio>0.22 and cooldown==0:
            cursor_index=(cursor_index+1)%len(emojis)
            cooldown=20
            both_eye_start=None

        # RIGHT BLINK → move left
        elif right_ratio<0.18 and left_ratio>0.22 and cooldown==0:
            cursor_index=(cursor_index-1)%len(emojis)
            cooldown=20
            both_eye_start=None

        # BOTH BLINK → select after 1 second
        elif left_ratio<0.18 and right_ratio<0.18:
            if both_eye_start is None:
                both_eye_start=time.time()
            elif time.time()-both_eye_start>1:
                selected_emoji=cursor_index
                show_selected=True
                selected_start=time.time()

                # ── FIX 6: all hardware writes go through arduino_send() ──
                if cursor_index==BELL_INDEX:
                    bell_active=True
                    arduino_send(BELL_START)

                if cursor_index==LED_INDEX:
                    bulb_on=not bulb_on
                    arduino_send(LIGHT_ON_CMD if bulb_on else LIGHT_OFF_CMD)

                if cursor_index==FAN_INDEX:
                    fan_on=not fan_on
                    arduino_send(FAN_ON_CMD if fan_on else FAN_OFF_CMD)

                if cursor_index==MUSIC_INDEX:
                    music_toggle=not music_toggle
                    if music_toggle:
                        threading.Thread(target=music_loop,daemon=True).start()
                    else:
                        pygame.mixer.music.stop()

                if cursor_index==CHAT_INDEX:
                    if manual_window_open:
                        close_manual_window()
                    else:
                        threading.Thread(target=show_manual,daemon=True).start()

                cooldown=30
                both_eye_start=None

        else:
            both_eye_start=None


    # ---------------- UI Screen ----------------
    screen=build_background(DISPLAY_HEIGHT,DISPLAY_WIDTH)

    margin=max(16,DISPLAY_WIDTH//70)
    gap=max(12,DISPLAY_WIDTH//110)
    panel_pad=max(14,DISPLAY_WIDTH//90)
    header_h=clamp(DISPLAY_HEIGHT//9,78,108)
    top_h=clamp(int(DISPLAY_HEIGHT*0.37),250,340)
    top_y=margin+header_h+gap
    bottom_y=top_y+top_h+gap
    bottom_h=DISPLAY_HEIGHT-bottom_y-margin

    camera_w=clamp(int(DISPLAY_WIDTH*0.22),260,340)
    selection_w=clamp(int(DISPLAY_WIDTH*0.29),340,460)
    status_w=clamp(int(DISPLAY_WIDTH*0.25),300,380)
    branding_w=DISPLAY_WIDTH-(margin*2)-(gap*3)-camera_w-selection_w-status_w
    branding_w=max(230,branding_w)

    camera_x=margin
    selection_x=camera_x+camera_w+gap
    status_x=selection_x+selection_w+gap
    brand_x=status_x+status_w+gap

    draw_panel(screen,(margin,margin),(DISPLAY_WIDTH-margin,margin+header_h),PANEL_COLOR)
    draw_panel(screen,(camera_x,top_y),(camera_x+camera_w,top_y+top_h),PANEL_COLOR)
    draw_panel(screen,(selection_x,top_y),(selection_x+selection_w,top_y+top_h),PANEL_COLOR)
    draw_panel(screen,(status_x,top_y),(status_x+status_w,top_y+top_h),PANEL_COLOR)
    draw_panel(screen,(brand_x,top_y),(DISPLAY_WIDTH-margin,top_y+top_h),PANEL_COLOR)
    draw_panel(screen,(margin,bottom_y),(DISPLAY_WIDTH-margin,DISPLAY_HEIGHT-margin),PANEL_COLOR)

    add_text(screen,"AICON Control Interface",(margin+28,margin+header_h//2+8),0.86,TEXT_PRIMARY,2,cv2.FONT_HERSHEY_COMPLEX)
    add_text(screen,"Professional eye-blink communication dashboard",(margin+30,margin+header_h-18),0.42,TEXT_MUTED,1)
    add_text(screen,"Theme: #010B13",(DISPLAY_WIDTH-margin-200,margin+42),0.5,ACCENT_COLOR,1)
    add_text(screen,"Responsive layout tuned for your laptop display",(DISPLAY_WIDTH-margin-370,margin+header_h-18),0.46,TEXT_MUTED,1)

    add_text(screen,"Live Camera",(camera_x+24,top_y+40),0.6,TEXT_PRIMARY,2,cv2.FONT_HERSHEY_COMPLEX)
    add_text(screen,"Patient monitoring feed" if camera_connected else "Offline preview mode",(camera_x+24,top_y+68),0.46,TEXT_MUTED,1)
    cam_size=min(camera_w-(panel_pad*2),top_h-110)
    cam_size=max(170,cam_size)
    cam=cv2.resize(frame,(cam_size,cam_size))
    cam_x=camera_x+(camera_w-cam_size)//2
    cam_y=top_y+88
    screen[cam_y:cam_y+cam_size,cam_x:cam_x+cam_size]=cam
    cv2.rectangle(screen,(cam_x,cam_y),(cam_x+cam_size,cam_y+cam_size),DIVIDER_COLOR,2,lineType=cv2.LINE_AA)

    add_text(screen,"Current Selection",(selection_x+24,top_y+40),0.6,TEXT_PRIMARY,2,cv2.FONT_HERSHEY_COMPLEX)

    active_name=emoji_names[cursor_index]
    active_label=emoji_labels.get(active_name,active_name.title())
    info_x=selection_x+panel_pad
    info_y=top_y+78
    info_w=selection_w-(panel_pad*2)
    info_h=top_h-108
    draw_panel(screen,(info_x,info_y),(info_x+info_w,info_y+info_h),CARD_COLOR)

    active_icon_size=min(info_h-18,info_w-44,210)
    active_icon_size=max(140,active_icon_size)
    active_icon=cv2.resize(emojis[cursor_index],(active_icon_size,active_icon_size))
    icon_x=info_x+(info_w-active_icon_size)//2
    icon_y=info_y+12
    screen[icon_y:icon_y+active_icon_size,icon_x:icon_x+active_icon_size]=active_icon

    label_scale=0.44 if len(active_label) > 10 else 0.5
    label_size=cv2.getTextSize(active_label,cv2.FONT_HERSHEY_COMPLEX,label_scale,2)[0]
    label_x=info_x+(info_w-label_size[0])//2
    add_text(screen,active_label,(label_x,info_y+info_h-14),label_scale,TEXT_PRIMARY,2,cv2.FONT_HERSHEY_COMPLEX)

    add_text(screen,"System Status",(status_x+24,top_y+40),0.6,TEXT_PRIMARY,2,cv2.FONT_HERSHEY_COMPLEX)
    add_text(screen,"Real-time device feedback",(status_x+24,top_y+68),0.46,TEXT_MUTED,1)
    status_inner_x=status_x+24
    status_inner_y=top_y+92
    status_gap=12
    chip_w=(status_w-48-status_gap)//2
    chip_h=min(70,(top_h-120-status_gap*2)//3)
    draw_status_chip(screen,status_inner_x,status_inner_y,chip_w,chip_h,"Light","ON" if bulb_on else "OFF",bulb_on)
    draw_status_chip(screen,status_inner_x+chip_w+status_gap,status_inner_y,chip_w,chip_h,"Fan","ON" if fan_on else "OFF",fan_on)
    draw_status_chip(screen,status_inner_x,status_inner_y+chip_h+status_gap,chip_w,chip_h,"Music","ACTIVE" if music_toggle else "IDLE",music_toggle)
    draw_status_chip(screen,status_inner_x+chip_w+status_gap,status_inner_y+chip_h+status_gap,chip_w,chip_h,"Alert","ACTIVE" if bell_active else "READY",bell_active,True)
    draw_status_chip(screen,status_inner_x,status_inner_y+(chip_h+status_gap)*2,chip_w,chip_h,"Camera","ONLINE" if camera_connected else "OFFLINE",camera_connected)
    draw_status_chip(screen,status_inner_x+chip_w+status_gap,status_inner_y+(chip_h+status_gap)*2,chip_w,chip_h,"Arduino","ONLINE" if arduino else "OFFLINE",bool(arduino))

    add_text(screen,"AICON",(brand_x+24,top_y+40),0.68,TEXT_PRIMARY,2,cv2.FONT_HERSHEY_COMPLEX)
    add_text(screen,"Project identity",(brand_x+24,top_y+68),0.44,TEXT_MUTED,1)
    brand_panel_w=DISPLAY_WIDTH-margin-brand_x
    logo_size=min(150,brand_panel_w-70,top_h-140)
    logo_size=max(90,logo_size)
    if logo is not None:
        brand_logo=cv2.resize(logo,(logo_size,logo_size))
        logo_x=brand_x+(brand_panel_w-logo_size)//2
        logo_y=top_y+92
        screen[logo_y:logo_y+logo_size,logo_x:logo_x+logo_size]=brand_logo
    add_text(screen,"Vision-driven assistive interface",(brand_x+24,top_y+top_h-22),0.46,TEXT_MUTED,1)

    if show_selected and time.time()-selected_start>=5:
        show_selected=False
        if bell_active:
            arduino_send(BELL_STOP)   # ← FIX 6: safe send
            bell_active=False

    add_text(screen,"Communication Panel",(margin+24,bottom_y+38),0.64,TEXT_PRIMARY,2,cv2.FONT_HERSHEY_COMPLEX)
    add_text(screen,"Clean action cards for faster recognition and professional presentation",(margin+24,bottom_y+66),0.48,TEXT_MUTED,1)

    grid_x=margin+24
    grid_y=bottom_y+86
    grid_w=DISPLAY_WIDTH-(margin*2)-48
    grid_h=DISPLAY_HEIGHT-margin-grid_y-18
    cols=6
    rows=3
    gap_x=max(10,DISPLAY_WIDTH//120)
    gap_y=max(10,DISPLAY_HEIGHT//90)
    card_w=(grid_w-gap_x*(cols-1))//cols
    card_h=(grid_h-gap_y*(rows-1))//rows

    for i,e in enumerate(emojis):
        x=grid_x+(i%cols)*(card_w+gap_x)
        y=grid_y+(i//cols)*(card_h+gap_y)
        is_active=i==cursor_index
        draw_panel(screen,(x,y),(x+card_w,y+card_h),CARD_ACTIVE if is_active else CARD_COLOR)
        icon_size=min(card_h-16,card_w//3,72)
        icon_size=max(48,icon_size)
        icon=cv2.resize(e,(icon_size,icon_size))
        icon_x=x+12
        icon_y=y+(card_h-icon_size)//2
        screen[icon_y:icon_y+icon_size,icon_x:icon_x+icon_size]=icon
        cv2.rectangle(screen,(icon_x,icon_y),(icon_x+icon_size,icon_y+icon_size),DIVIDER_COLOR,1,lineType=cv2.LINE_AA)
        label=emoji_labels.get(emoji_names[i],emoji_names[i].title())
        text_x=icon_x+icon_size+12
        label_scale=0.4 if card_w < 210 else 0.46
        meta_scale=0.36 if card_h < 84 else 0.4
        add_text(screen,label,(text_x,y+card_h//2-4),label_scale,TEXT_PRIMARY,2 if is_active else 1)
        add_text(screen,emoji_names[i].upper(),(text_x,y+card_h//2+20),meta_scale,ACCENT_COLOR if is_active else TEXT_MUTED,1)
        if is_active:
            cv2.rectangle(screen,(x,y),(x+card_w,y+card_h),ACCENT_COLOR,2,lineType=cv2.LINE_AA)

    cv2.imshow("AI Assist Screen",screen)

    if cv2.waitKey(1)==27:
        break


# ---------------- Cleanup ----------------
if cap is not None:
    cap.release()
cv2.destroyAllWindows()
pygame.mixer.quit()

# FIX 7: safe cleanup — close port only if it is still open
with _arduino_lock:
    if arduino and arduino.isOpen():
        arduino.close()
        print("[Arduino] Port closed cleanly.")
