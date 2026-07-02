"""
AIおはなし機「ねえねえ」メインスクリプト（PC版）

ウェイクワード:
  "hey jarvis"  → お話のリクエストを聞く
  "hey mycroft" → 現在のお話をさらにへんてこに展開
  "alexa"       → お話を締めくくって終了

使い方：
    python main.py

終了：Ctrl+C
"""

import io
import os
import struct
import threading
import time
import json
import numpy as np
import pyaudio
import pygame
import websocket
from openwakeword.model import Model
from openai import OpenAI

# ─── APIキー設定 ─────────────────────────────────────
OPENAI_API_KEY   = "YOUR API KEY"   # ← OpenAI APIキー
AMIVOICE_APP_KEY = "YOUR APP KEY" # ← AmiVoice AppKey
# ────────────────────────────────────────────────────

# ウェイクワード設定
import openwakeword
MODEL_DIR = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
WAKE_WORD_PATHS = [
    os.path.join(MODEL_DIR, "hey_jarvis_v0.1.onnx"),
    os.path.join(MODEL_DIR, "hey_mycroft_v0.1.onnx"),
    os.path.join(MODEL_DIR, "alexa_v0.1.onnx"),
]
WAKE_THRESHOLD = 0.5

# AmiVoice設定
AMIVOICE_WS_URL = "wss://acp-api.amivoice.com/v1/"
AMIVOICE_ENGINE = "-a-general"
AUDIO_FORMAT    = "16K"

# 録音設定
SAMPLE_RATE        = 16000
CHUNK              = 1280   # openWakeWord用（80ms）
SILENCE_THRESHOLD  = 200
SILENCE_DURATION   = 1.5
MAX_RECORD_SECONDS = 10
AUDIO_BLOCK_SIZE   = 16000

# TTS設定
TTS_VOICE = "nova"
TTS_SPEED = 0.9

# GPT設定
SYSTEM_PROMPT = """
あなたは子ども向けのへんてこなお話を作るAIです。

ルール：
- 4歳前後の子どもに向けた、やさしい言葉を使う
- 1回のお話は100〜200文字程度（読み上げ約30秒）
- 必ず1つ「へんてこな出来事」を入れる（例：カレーが空から降る、ぞうがロケットに乗る）
- 文末は「〜でした」「〜しました」など、お話らしい口調にする
- 最後は「おしまい」で締めくくる
"""


# ─── 音声認識（AmiVoice）─────────────────────────────

def record_audio(pa):
    """無音検出付き録音"""
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=1024
        # PC環境ではinput_device_indexを指定しない（デフォルトマイクを使用）
    )
    print("  🎤 話しかけてください...")
    frames = []
    silent_chunks = 0
    silent_limit = int(SAMPLE_RATE / 1024 * SILENCE_DURATION)
    max_chunks   = int(SAMPLE_RATE / 1024 * MAX_RECORD_SECONDS)
    has_voice = False

    for _ in range(max_chunks):
        data = stream.read(1024, exception_on_overflow=False)
        frames.append(data)
        amplitude = max(abs(x) for x in struct.unpack("1024h", data))
        if amplitude >= SILENCE_THRESHOLD:
            has_voice = True
            silent_chunks = 0
        else:
            if has_voice:
                silent_chunks += 1
        if has_voice and silent_chunks >= silent_limit:
            break

    stream.stop_stream()
    stream.close()
    return b"".join(frames), has_voice


def amivoice_recognize(audio_bytes):
    """AmiVoice WebSocket APIで音声認識"""
    result_text = ""
    done_event  = threading.Event()

    def on_open(ws):
        command = f"s {AUDIO_FORMAT} {AMIVOICE_ENGINE} authorization={AMIVOICE_APP_KEY}"
        ws.send(command)

    def on_message(ws, message):
        nonlocal result_text
        event   = message[0]
        content = message[2:].rstrip()

        if event == 's':
            if content != "":
                print(f"  ❌ AmiVoiceエラー: {content}")
                done_event.set()
                ws.close()
                return
            def send_audio():
                offset = 0
                while offset < len(audio_bytes):
                    chunk = audio_bytes[offset:offset + AUDIO_BLOCK_SIZE]
                    ws.send(b'p' + chunk, opcode=websocket.ABNF.OPCODE_BINARY)
                    offset += AUDIO_BLOCK_SIZE
                    time.sleep(0.5)
                ws.send('e')
            threading.Thread(target=send_audio).start()

        elif event in ('A', 'R'):
            raw = json.loads(content) if content else {}
            result_text = raw.get("text", "")

        elif event == 'e':
            done_event.set()
            ws.close()

        elif event == 'p' and content:
            done_event.set()
            ws.close()

    def on_error(ws, error):
        done_event.set()

    def on_close(ws, *args):
        done_event.set()

    ws = websocket.WebSocketApp(
        AMIVOICE_WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
    )
    t = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 0})
    t.daemon = True
    t.start()
    done_event.wait(timeout=60)
    return result_text.strip()


# ─── お話生成（GPT-4o）───────────────────────────────

def generate_story(client, request: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=400,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"「{request}」のお話を作って"}
        ]
    )
    return response.choices[0].message.content


def make_weirder(client, current_story: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=400,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"このお話の続きを、もっとへんてこな方向に展開させて：\n\n{current_story}"}
        ]
    )
    return response.choices[0].message.content


def make_ending(client, current_story: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=400,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"このお話をきれいに締めくくって：\n\n{current_story}"}
        ]
    )
    return response.choices[0].message.content


# ─── 読み上げ（OpenAI TTS）──────────────────────────

def speak(client, text: str):
    print(f"  🔊 読み上げ中...")
    response = client.audio.speech.create(
        model="tts-1",
        voice=TTS_VOICE,
        input=text,
        speed=TTS_SPEED
    )
    audio_data = io.BytesIO(response.content)
    pygame.mixer.music.load(audio_data)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.wait(100)


# ─── ウェイクワード用ストリームを開く ────────────────

def open_ww_stream(pa):
    return pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK
        # PC環境ではinput_device_indexを指定しない
    )


# ─── メインループ ────────────────────────────────────

def main():
    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY" or AMIVOICE_APP_KEY == "YOUR_AMIVOICE_APP_KEY":
        print("❌ APIキーを設定してください（main.py の先頭）")
        return

    pygame.mixer.init()
    client = OpenAI(api_key=OPENAI_API_KEY)
    pa     = pyaudio.PyAudio()

    print("モデルをロード中...")
    oww = Model(
        wakeword_models=["hey_jarvis", "hey_mycroft", "alexa"],
        inference_framework="onnx"
    )

    ww_stream = open_ww_stream(pa)
    current_story = ""

    print("\n=== AIおはなし機 起動 ===")
    print("「hey jarvis」  → お話のリクエストを聞く")
    print("「hey mycroft」 → もっとへんてこに展開")
    print("「alexa」       → お話を締めくくる")
    print("終了: Ctrl+C\n")
    print("待機中...")

    try:
        while True:
            # ウェイクワード検出
            audio_data  = ww_stream.read(CHUNK, exception_on_overflow=False)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            prediction  = oww.predict(audio_array)

            keyword_index = -1
            for i, (model_name, score) in enumerate(prediction.items()):
                if score >= WAKE_THRESHOLD:
                    keyword_index = i
                    print(f"\n✅ ウェイクワード検出: [{model_name}] スコア={score:.2f}")
                    break

            if keyword_index == -1:
                continue

            # ウェイクワード用ストリームを一時停止・解放
            ww_stream.stop_stream()
            ww_stream.close()

            # ────── index 0: リクエストを聞く ──────
            if keyword_index == 0:
                speak(client, "何のお話がいい？動物でも食べ物でもなんでもいいよ！")
                audio_bytes, has_voice = record_audio(pa)

                if not has_voice:
                    speak(client, "ごめんね、聞こえなかったよ")
                else:
                    request = amivoice_recognize(audio_bytes)
                    print(f"  📝 リクエスト: 「{request}」")

                    if not request:
                        speak(client, "ごめんね、もう一回言ってね")
                    else:
                        speak(client, "わかった！じゃあはじまるよ〜")
                        story = generate_story(client, request)
                        current_story = story
                        print(f"\n📖 お話:\n{story}\n")
                        speak(client, story)

            # ────── index 1: もっとへんてこに ──────
            elif keyword_index == 1:
                if not current_story:
                    speak(client, "まだお話してないよ。ヘイジャービスって呼んでね")
                else:
                    speak(client, "もっとへんにする？いくよ〜")
                    continuation = make_weirder(client, current_story)
                    current_story += "\n" + continuation
                    print(f"\n📖 続き:\n{continuation}\n")
                    speak(client, continuation)

            # ────── index 2: おわり ──────────────
            elif keyword_index == 2:
                if current_story:
                    speak(client, "じゃあおわりにするね")
                    ending = make_ending(client, current_story)
                    print(f"\n📖 締め:\n{ending}\n")
                    speak(client, ending)
                    current_story = ""
                    speak(client, "おしまい！またヘイジャービスって呼んでね")

            # ウェイクワード用ストリームを再開
            ww_stream = open_ww_stream(pa)
            print("\n待機中...")

    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        ww_stream.stop_stream()
        ww_stream.close()
        pa.terminate()
        pygame.mixer.quit()


if __name__ == "__main__":
    main()
