"""
AIおはなし機「ねえねえ」メインスクリプト（PC版）v0.5

操作方法:
  Enter      → お話スタート
  q + Enter  → お話を途中終了する
  Ctrl+C     → プログラム終了

フロー:
  1. Enter → TTS：おはなしはじまるよ〜
  2. TTS：「きょうは なんの おはなしにする？〇〇と △△、どっちでもいいよ！」
  3. 録音（3秒）→ keyword抽出 → お話生成
  4. 続きターン：TTS：「つぎは どうなる？〇〇する？それとも △△する？」
  5. ターン4：全文読み上げ → バイバイ → 待機に戻る
"""

import csv
import io
import json
import random
import struct
import threading
import time
from pathlib import Path

import pyaudio
import pygame
import websocket
from openai import OpenAI
from themes import build_opening_prompt, get_random_choices, get_same_genre_choices

# ─── APIキー設定 ─────────────────────────────────────
OPENAI_API_KEY   = "YOUR API KEY"
AMIVOICE_APP_KEY = "YOUR API KEY"

# ─── AmiVoice設定 ──────────────────────────────────
AMIVOICE_WS_URL = "wss://acp-api.amivoice.com/v1/"
AMIVOICE_ENGINE = "-a-general"
AUDIO_FORMAT    = "16K"

# ─── フィラー除去設定 ──────────────────────────────
# True  = フィラーを除去する（デフォルト・GPTへの入力がクリーンになる）
# False = フィラーを残す（「えっとね〜」などがそのまま認識結果に出る）
# 検証時はセッションごとに切り替えてログを比較する
REMOVE_FILLER = False

# ─── 録音設定 ──────────────────────────────────────
SAMPLE_RATE              = 16000
SILENCE_THRESHOLD        = 200
SILENCE_DURATION         = 0.7
MAX_RECORD_SECONDS_SHORT = 3   # リクエスト録音（最初）
MAX_RECORD_SECONDS_LONG  = 5   # 返答録音（続き）
AUDIO_BLOCK_SIZE         = 16000

# ─── TTS設定 ───────────────────────────────────────
TTS_MODEL        = "gpt-4o-mini-tts-2025-12-15"
TTS_VOICE        = "nova"
PAUSE_AFTER_STORY = 1.0  # お話と質問の間の溜め（秒）

# ─── 会話設定 ─────────────────────────────────────
MAX_TURNS            = 3   # 続きの最大ターン数（3回後に全文読み上げ）
RETRY_LIMIT          = 2   # 認識失敗のリトライ上限

# ─── 会話レベル設定 ────────────────────────────────
LEVEL_UP_THRESHOLD   = 3
LEVEL_DOWN_THRESHOLD = 2

# ─── GPTプロンプト ─────────────────────────────────

KEYWORD_EXTRACT_PROMPT = """
以下の文章から、お話のテーマになりそうな単語を1つだけ抜き出してください。

ルール:
- 単語だけ返す
- 名詞を優先
- ひらがなでもOK
- わからなければ「ふしぎ」

例:
入力: 「ライオンって言ってごらん」
出力: ライオン
"""

HIRAGANA_PROMPT = """
以下のテキストを、4〜6歳の子どもが聞いても理解できるように変換してください。

ルール:
- 漢字はすべてひらがなに変換する
- カタカナはそのまま残す
- 句読点・記号はそのまま残す
- 内容は変えない
- 変換したテキストだけを返す（説明不要）

例:
入力: 「うさぎが空を飛んだ！どう思う？」
出力: 「うさぎが そらを とんだ！どう おもう？」
"""

# お話と質問を「---」で区切って返すよう指示
STORY_PROMPT_L1 = """
あなたは4〜6歳児向けのへんてこなお話AIです。

ルール:
- お話は1〜2文（20〜40文字）、ひらがな多め、必ず変な出来事を入れる
- 質問は最後に2択で（選ばせる）
- お話と質問を「---」で区切って出力する

テーマ: {keyword}

出力形式:
ぞうさんが そらを とんでるよ！
---
たかいほうと ひくいほう、どっちがすき？
"""

STORY_PROMPT_L2 = """
あなたは4〜6歳児向けのへんてこなお話AIです。

ルール:
- お話は1〜2文（20〜40文字）、ひらがな多め、必ず変な出来事を入れる
- 質問は最後に単語で答えられる形式
- お話と質問を「---」で区切って出力する

テーマ: {keyword}

出力形式:
ライオンさんが バナナを100ほん たべちゃった！
---
おなか どうなったとおもう？
"""

STORY_PROMPT_L3 = """
あなたは4〜6歳児向けのへんてこなお話AIです。

ルール:
- お話は1〜2文（20〜40文字）、ひらがな多め、必ず変な出来事を入れる
- 質問は最後に自由に答えられる形式
- お話と質問を「---」で区切って出力する

テーマ: {keyword}

出力形式:
ねこさんが ピザを100まい やいちゃった！
---
つぎ なにが おきると おもう？
"""

CONTINUE_PROMPT = """
あなたは4〜6歳児向けお話AIです。

これまでのお話の流れ:
{story_log}

ルール:
- 直前の「子どもの返答」を必ずストーリーに自然に組み込んで続きを書く
- 登場人物・場所・出来事など前のターンの要素を引き継ぐ（話が切れないように）
- お話は2〜3文（40〜50文字）、やさしい言葉、へんてこな展開を入れる
- ひらがな多め
- 文末は「〜でした」「〜しました」など
- 質問は最後にまた1つ聞く
- お話と質問を「---」で区切って出力する

出力例（ライオンがバナナを食べた話で子どもが「ぱんぱん！」と言った場合）:
ぱんぱんに なった ライオンさんは、おなかが ふうせんみたいに なって そらに とんじゃった！
---
どこまで とんでいくと おもう？
"""

CHOICES_PROMPT = """
あなたは4〜6歳児向けお話AIです。

これまでのお話:
{story_log}

このお話の続きとして、子どもが選べる2つの展開を考えてください。

ルール:
- 2択をひらがな・カタカナで短く（各10文字以内）
- 「---」で区切って2つだけ出力する
- お話の流れに自然につながる展開にする

出力例:
そらを とぶ
---
うみに もぐる
"""

FAIL_MESSAGES = [
    "もうちょっと ちかくで おはなしして〜",
    "ちいさい こえだったかも！",
    "もういっかい おねがい！",
]


# ─── ユーティリティ ────────────────────────────────

def get_story_prompt(level: int, keyword: str) -> str:
    if level == 1:
        return STORY_PROMPT_L1.format(keyword=keyword)
    elif level == 2:
        return STORY_PROMPT_L2.format(keyword=keyword)
    else:
        return STORY_PROMPT_L3.format(keyword=keyword)


def split_story_question(text: str):
    """GPT出力を「---」でお話と質問に分割する"""
    if "---" in text:
        parts = text.split("---", 1)
        return parts[0].strip(), parts[1].strip()
    # 区切りがない場合は全文をお話として返す
    return text.strip(), ""


# ─── 音声録音 ─────────────────────────────────────

def record_audio(pa: pyaudio.PyAudio, max_seconds: int):
    """無音検出付き録音"""
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=1024
    )
    print("  🎤 話しかけてください...")
    frames        = []
    silent_chunks = 0
    silent_limit  = int(SAMPLE_RATE / 1024 * SILENCE_DURATION)
    max_chunks    = int(SAMPLE_RATE / 1024 * max_seconds)
    has_voice     = False

    for _ in range(max_chunks):
        data = stream.read(1024, exception_on_overflow=False)
        frames.append(data)
        amplitude = max(abs(x) for x in struct.unpack("1024h", data))
        if amplitude >= SILENCE_THRESHOLD:
            has_voice     = True
            silent_chunks = 0
        else:
            if has_voice:
                silent_chunks += 1
        if has_voice and silent_chunks >= silent_limit:
            break

    stream.stop_stream()
    stream.close()
    return b"".join(frames), has_voice


# ─── AmiVoice認識 ─────────────────────────────────

def amivoice_recognize(audio_bytes: bytes) -> str:
    """AmiVoice WebSocket APIで音声認識"""
    result_text = ""
    done_event  = threading.Event()

def amivoice_recognize(audio_bytes: bytes) -> dict:
    """
    AmiVoice WebSocket APIで音声認識。
    戻り値: {"text": str, "spoken": str}
      text   = 書き言葉（漢字まじり）
      spoken = 読み（ひらがな・カタカナ）← 子ども向け活用に有効
    """
    result = {"text": "", "spoken": ""}
    done_event = threading.Event()

    def on_open(ws):
        filler_param = "" if REMOVE_FILLER else " d keepFillerToken=1"
        command = f"s {AUDIO_FORMAT} {AMIVOICE_ENGINE} authorization={AMIVOICE_APP_KEY}{filler_param}"
        ws.send(command)

    def on_message(ws, message):
        nonlocal result
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
                    time.sleep(0.2)
                ws.send('e')

            threading.Thread(target=send_audio, daemon=True).start()

        elif event in ('A', 'R'):
            raw = json.loads(content) if content else {}
            result["text"] = raw.get("text", "")
            # tokensからspoken（読み）を結合
            tokens = raw.get("results", [{}])[0].get("tokens", []) if raw.get("results") else []
            spoken_parts = [t.get("spoken", t.get("written", "")) for t in tokens]
            result["spoken"] = "".join(spoken_parts)

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
    t = threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 0}, daemon=True)
    t.start()
    done_event.wait(timeout=30)
    result["text"]   = result["text"].strip()
    result["spoken"] = result["spoken"].strip()
    return result


# ─── GPT処理 ──────────────────────────────────────

def to_hiragana(client: OpenAI, text: str) -> str:
    """漢字をひらがなに変換（TTS読み間違い対策）"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=200,
        messages=[
            {"role": "system", "content": HIRAGANA_PROMPT},
            {"role": "user",   "content": text}
        ]
    )
    return response.choices[0].message.content.strip()


def extract_keyword(client: OpenAI, text: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=10,
        messages=[
            {"role": "system", "content": KEYWORD_EXTRACT_PROMPT},
            {"role": "user",   "content": text}
        ]
    )
    return response.choices[0].message.content.strip()


def generate_story(client: OpenAI, keyword: str, level: int, genre: str = "") -> str:
    """genreを渡すとプロンプトに追加してテーマを強調する"""
    prompt = get_story_prompt(level, keyword)
    if genre:
        prompt += f"\n\nジャンルのヒント: {genre}のお話にする（キーワードが不明でもこのジャンルを忘れないで）"
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=120,
        messages=[{"role": "system", "content": prompt}]
    )
    return response.choices[0].message.content.strip()


def continue_story(client: OpenAI, story_log: list) -> str:
    """
    story_log: [{"role": "story"|"child", "text": str}, ...]
    """
    # ログをテキスト形式に整形してGPTに渡す
    log_text = ""
    for entry in story_log:
        if entry["role"] == "story":
            log_text += f"【おはなし】{entry['text']}\n"
        else:
            log_text += f"【こどもの返答】{entry['text']}\n"

    prompt = CONTINUE_PROMPT.format(story_log=log_text.strip())
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=120,
        messages=[{"role": "system", "content": prompt}]
    )
    return response.choices[0].message.content.strip()


def generate_choices(client: OpenAI, story_log: list) -> tuple[str, str]:
    """
    ストーリーの流れに合わせた2択の展開を生成する。
    戻り値: (選択肢A, 選択肢B)
    """
    log_text = ""
    for entry in story_log:
        if entry["role"] == "story":
            log_text += f"【おはなし】{entry['text']}\n"
        else:
            log_text += f"【こどもの返答】{entry['text']}\n"

    prompt = CHOICES_PROMPT.format(story_log=log_text.strip())
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=30,
        messages=[{"role": "system", "content": prompt}]
    )
    raw = response.choices[0].message.content.strip()
    if "---" in raw:
        parts = raw.split("---", 1)
        return parts[0].strip(), parts[1].strip()
    # 分割できない場合はテーマリストからランダムに返す
    _, a, b = get_random_choices()
    return a, b


# ─── TTS ──────────────────────────────────────────

def speak(client: OpenAI, text: str, convert_hiragana: bool = False):
    """テキストをTTSで読み上げる"""
    tts_text = to_hiragana(client, text) if convert_hiragana else text
    print(f"  🔊 {tts_text}")
    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=tts_text
    )
    audio_data = io.BytesIO(response.content)
    pygame.mixer.music.load(audio_data)
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        pygame.time.wait(100)


def speak_story_and_question(client: OpenAI, story: str, question: str):
    """お話と質問の間に溜めを入れて読み上げる"""
    speak(client, story, convert_hiragana=True)
    if question:
        time.sleep(PAUSE_AFTER_STORY)  # お話と質問の間の溜め
        speak(client, question, convert_hiragana=True)


# ─── ログ ────────────────────────────────────────

LOG_FILE  = Path("ohanashi_log.csv")
WAV_DIR   = Path("recordings")
LOG_FIELDS = ["timestamp", "event", "keyword", "level",
              "recognized_text", "intended_word", "match",
              "filler_mode", "latency", "wav_file"]

def init_log():
    """CSVファイルとrecordingsディレクトリを初期化"""
    WAV_DIR.mkdir(exist_ok=True)
    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            writer.writeheader()
        print(f"  📋 ログファイル作成: {LOG_FILE}")
    print(f"  🎙️  録音保存先: {WAV_DIR}/")


def save_wav(audio_bytes: bytes, label: str) -> str:
    """
    audio_bytesをWAVファイルとして保存する。
    ファイル名: recordings/YYYYMMDD_HHMMSS_{label}.wav
    戻り値: ファイル名（CSVのwav_file列に記録）
    """
    import wave
    ts       = time.strftime("%Y%m%d_%H%M%S")
    filename = WAV_DIR / f"{ts}_{label}.wav"
    with wave.open(str(filename), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_bytes)
    print(f"  💾 録音保存: {filename.name}")
    return filename.name


def log(event: str, data: dict):
    """コンソール表示 + CSVに追記"""
    entry = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
    entry.update({k: data.get(k, "") for k in LOG_FIELDS if k != "timestamp"})
    # match列：recognized_textとintended_wordが一致するか（両方ある場合）
    if entry.get("recognized_text") and entry.get("intended_word"):
        entry["match"] = "1" if entry["recognized_text"] == entry["intended_word"] else "0"
    print(f"  📋 LOG: {entry}")
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        writer.writerow(entry)

def ask_intended_word(recognized: str) -> str:
    """認識結果を表示して、実際に言った言葉を親が入力する"""
    print(f"  👆 実際に何と言いましたか？（そのままEnterでスキップ）: ", end="", flush=True)
    intended = input().strip()
    return intended if intended else recognized


# ─── お話セッション ───────────────────────────────

def run_story_session(client: OpenAI, pa: pyaudio.PyAudio, conv_level: int):
    """
    1回のお話セッションを実行する。
    戻り値: (success_delta, fail_delta)
    """
    # ── イントロ＋テーマ提案（一息で）──
    genre, choice_a, choice_b = get_random_choices()
    opening_text = (
        f"おはなし はじまるよ〜。"
        f"きょうは なんの おはなしにしようかなあ。"
        f"{genre}は どう？{choice_a}と {choice_b}とか、、、なんでもいいよ～"
    )
    speak(client, opening_text)

    # ── キーワード取得（リトライあり）──
    keyword = None
    for attempt in range(RETRY_LIMIT + 1):
        audio_bytes, has_voice = record_audio(pa, MAX_RECORD_SECONDS_SHORT)

        if not has_voice:
            ca, cb = get_same_genre_choices(genre, [choice_a, choice_b])
            speak(client, f"きこえなかったよ〜。{ca}とか {cb}とか、、、なんでもいいよ～")
            audio_bytes, has_voice = record_audio(pa, MAX_RECORD_SECONDS_SHORT)
            if not has_voice:
                continue

        rec            = amivoice_recognize(audio_bytes)
        request        = rec["text"]
        request_spoken = rec["spoken"]
        print(f"  📝 認識: 「{request}」 / 読み: 「{request_spoken}」")

        if not request and not request_spoken:
            ca, cb = get_same_genre_choices(genre, [choice_a, choice_b])
            speak(client, f"きこえなかったよ〜。{ca}とか {cb}とか、、、なんでもいいよ～")
            continue

        wav_name = save_wav(audio_bytes, f"keyword_attempt{attempt+1}")
        intended = ask_intended_word(request)
        # spokenを優先してキーワード抽出（漢字誤変換の影響を受けにくい）
        keyword_source = request_spoken if request_spoken else request
        keyword = extract_keyword(client, keyword_source)
        print(f"  🎯 キーワード: {keyword}（抽出元: {'spoken' if request_spoken else 'text'}）")
        log("keyword_request", {
            "recognized_text": request,
            "intended_word": intended,
            "keyword": keyword,
            "level": conv_level,
            "filler_mode": "remove" if REMOVE_FILLER else "keep",
            "wav_file": wav_name,
        })
        break

    if not keyword:
        speak(client, "うまく きこえなかったよ〜。またあそぼうね！")
        return 0, RETRY_LIMIT + 1

    # ── お話スタート ──
    speak(client, f"わかった！{keyword}だね！じゃあはじまるよ〜")

    # ── ターン1〜MAX_TURNS ──
    story_log = []
    t_start   = time.time()

    raw = generate_story(client, keyword, conv_level, genre=genre)
    story_part, question_part = split_story_question(raw)
    story_log.append({"role": "story", "text": story_part})
    print(f"\n📖 お話 ターン1 (Lv{conv_level}):\n{raw}\n")
    speak_story_and_question(client, story_part, question_part)

    log("story_generated", {
        "keyword": keyword,
        "level": conv_level,
        "latency": round(time.time() - t_start, 2)
    })

    for turn in range(2, MAX_TURNS + 2):  # ターン2〜4
        # ── GPT生成の2択候補（毎回新たに生成）──
        ca, cb = generate_choices(client, story_log)
        print(f"  🎲 2択候補: {ca} / {cb}")

        # ── 子どもの返答を録音 ──
        reply = None
        for attempt in range(RETRY_LIMIT + 1):
            audio_bytes, has_voice = record_audio(pa, MAX_RECORD_SECONDS_LONG)

            if not has_voice:
                speak(client, f"{ca}とか、{cb}とか・・・なんでもいいよ～")
                audio_bytes, has_voice = record_audio(pa, MAX_RECORD_SECONDS_LONG)
                if not has_voice:
                    continue

            rec        = amivoice_recognize(audio_bytes)
            recognized = rec["text"]
            rec_spoken = rec["spoken"]
            print(f"  📝 返答: 「{recognized}」 / 読み: 「{rec_spoken}」")

            if not recognized and not rec_spoken:
                speak(client, f"{ca}とか、{cb}とか・・・なんでもいいよ～")
                audio_bytes, has_voice = record_audio(pa, MAX_RECORD_SECONDS_LONG)
                if not has_voice:
                    continue
                rec        = amivoice_recognize(audio_bytes)
                recognized = rec["text"]
                rec_spoken = rec["spoken"]
                if not recognized and not rec_spoken:
                    continue

            wav_name = save_wav(audio_bytes, f"turn{turn}_attempt{attempt+1}")
            intended  = ask_intended_word(recognized)
            log("child_reply", {
                "recognized_text": recognized,
                "intended_word": intended,
                "keyword": keyword,
                "level": conv_level,
                "filler_mode": "remove" if REMOVE_FILLER else "keep",
                "wav_file": wav_name,
            })
            # GPTには読みを優先して渡す
            reply = rec_spoken if rec_spoken else recognized
            break

        # ターン4（最終）or リトライ失敗 → 全文読み上げ＆終了
        if turn > MAX_TURNS or reply is None:
            break

        # 子どもの返答をログに追加
        story_log.append({"role": "child", "text": reply})

        # ── 続きを生成（ストーリーログ全体を渡す）──
        raw = continue_story(client, story_log)
        story_part, question_part = split_story_question(raw)
        story_log.append({"role": "story", "text": story_part})
        print(f"\n📖 お話 ターン{turn} (Lv{conv_level}):\n{raw}\n")
        speak_story_and_question(client, story_part, question_part)

    # ── エンディング：全文読み上げ ──
    full_story = "　".join(
        entry["text"] for entry in story_log if entry["role"] == "story"
    )
    speak(client, "ここまで つくった おはなし、ぜんぶ よむね！")
    time.sleep(0.5)
    speak(client, full_story, convert_hiragana=True)
    time.sleep(0.5)
    speak(client, "とっても おもしろい おはなしが できたね！またつくろうね、ばいばーい！")

    return 1, 0


# ─── メイン ───────────────────────────────────────

def main():
    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY" or AMIVOICE_APP_KEY == "YOUR_AMIVOICE_APP_KEY":
        print("❌ APIキーを設定してください（main_pc.py の先頭）")
        return

    pygame.mixer.init()
    init_log()
    client = OpenAI(api_key=OPENAI_API_KEY)
    pa     = pyaudio.PyAudio()

    conv_level    = 2
    success_count = 0
    fail_count    = 0

    print("\n=== AIおはなし機 起動（PC版）v0.5 ===")
    print("Enter      → お話スタート")
    print("q + Enter  → お話を途中終了")
    print("Ctrl+C     → プログラム終了")
    print(f"会話レベル: {conv_level}")
    print(f"フィラー除去: {'ON（除去する）' if REMOVE_FILLER else 'OFF（残す）'}")
    print("※ フィラー設定を変えるには REMOVE_FILLER を True/False で切り替えてください\n")

    try:
        while True:
            print("─" * 40)
            print(f"待機中... Lv{conv_level}  [Enter=スタート / q=やめる]")
            key = input().strip().lower()

            if key == "q":
                print("終了します")
                break

            if key != "":
                print("  ⚠️  Enterでスタート、qでやめる")
                continue

            # ── お話セッション実行 ──
            success_delta, fail_delta = run_story_session(client, pa, conv_level)
            success_count += success_delta
            fail_count    += fail_delta

            # ── 会話レベル調整 ──
            if success_delta > 0:
                fail_count = 0
                if success_count >= LEVEL_UP_THRESHOLD and conv_level < 3:
                    conv_level    += 1
                    success_count  = 0
                    print(f"  ⬆️  会話レベル上昇: {conv_level}")
            if fail_delta > 0:
                success_count = 0
                if fail_count >= LEVEL_DOWN_THRESHOLD and conv_level > 1:
                    conv_level = max(1, conv_level - 1)
                    fail_count = 0
                    print(f"  ⬇️  会話レベル下降: {conv_level}")

    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        pa.terminate()
        pygame.mixer.quit()


if __name__ == "__main__":
    main()
