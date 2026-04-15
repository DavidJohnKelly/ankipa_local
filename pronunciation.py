import json
import wave
import os
import tempfile
import difflib
import re

from vosk import Model, KaldiRecognizer
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein
from scipy import signal

import eng_to_ipa as ipa
import numpy as np

from functools import lru_cache

from .ankipa import AnkiPA


MODEL_PATH = os.path.join(
    os.path.dirname(__file__),
    "models",
    "vosk-model-small-en-us-0.15",
)

_VOSK_MODEL = None

_NON_IPA = re.compile(r"[^a-zɪʊɛɔæʌəɑθðʃʒŋ ]+")

def init_pronunciation_engine():
    global _VOSK_MODEL

    if _VOSK_MODEL is None:
        if not os.path.isdir(MODEL_PATH):
            raise FileNotFoundError(f"Vosk model directory not found: {MODEL_PATH}")
        _VOSK_MODEL = Model(MODEL_PATH)


@lru_cache(maxsize=2048)
def _get_phones(word: str):
    try:
        # eng_to_ipa returns word* if not found; we strip that asterisk
        ipa_str = ipa.convert(word.lower()).rstrip('*')

        # remove stress + junk
        ipa_str = ipa_str.replace("ˈ", "").replace("ˌ", "")
        ipa_str = _NON_IPA.sub("", ipa_str)

        return [c for c in ipa_str if c.strip()]
    except Exception as e:
        print(f"[AnkiPA] IPA failed for '{word}': {e}")
        return []


def _ensure_wav_16k_mono(src_path: str) -> str:
    with wave.open(src_path, "rb") as w:
        nchannels, sampwidth, framerate, nframes, _, _ = w.getparams()
        frames = w.readframes(nframes)

    if sampwidth == 1:
        audio_data = np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128
    elif sampwidth == 2:
        audio_data = np.frombuffer(frames, dtype=np.int16)
    else:
        raise ValueError("Unsupported sample width")

    if nchannels > 1:
        audio_data = audio_data.reshape(-1, nchannels)
        audio_data = np.mean(audio_data, axis=1).astype(np.int16)

    if framerate != 16000:
        num_samples = int(len(audio_data) * 16000 / framerate)
        audio_data = signal.resample(audio_data, num_samples).astype(np.int16)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = tmp.name
    tmp.close()

    with wave.open(tmp_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(audio_data.tobytes())

    return tmp_path


def _tokenise(text: str):
    return [
        t for t in "".join(
            c if c.isalnum() or c.isspace() else " " for c in text
        ).split()
        if t
    ]


def pron_assess(reference_text, recorded_voice):
    try:
        init_pronunciation_engine()
    except Exception as e:
        return {"error": f"Engine init failed: {e}"}

    wav_path = None

    try:
        wav_path = _ensure_wav_16k_mono(recorded_voice)

        rec = KaldiRecognizer(_VOSK_MODEL, 16000.0)
        rec.SetWords(True)

        recognised = []

        with wave.open(wav_path, "rb") as wf:
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    recognised.extend(json.loads(rec.Result()).get("result", []))
            recognised.extend(json.loads(rec.FinalResult()).get("result", []))

    except Exception as e:
        return {"error": f"Recognition failed: {e}"}
    finally:
        if wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)

    orig_ref_words = _tokenise(reference_text)
    ref_words = [w.lower() for w in orig_ref_words]
    rec_words = [r["word"].lower() for r in recognised]

    display_words = [r["word"] for r in recognised]
    if display_words:
        display_words[0] = display_words[0].capitalize()

    recognized_text = " ".join(display_words)

    rec_starts = [r.get("start", 0.0) for r in recognised]
    rec_ends = [r.get("end", 0.0) for r in recognised]

    # Pre-cache reference phones
    ref_phones_map = {w: _get_phones(w) for w in set(ref_words)}

    def calculate_word_score(ref_w, rec_w):
        ref_ph = ref_phones_map.get(ref_w, [])
        rec_ph = _get_phones(rec_w)
        
        if ref_ph or rec_ph:
            dist = Levenshtein.distance(ref_ph, rec_ph)
            max_len = max(len(ref_ph), len(rec_ph))
            phone_sim = 100 * (1 - dist / max_len) if max_len else 0
        else:
            phone_sim = 0

        orth_sim = fuzz.ratio(ref_w, rec_w)
        return int(round(0.7 * phone_sim + 0.3 * orth_sim))

    sm = difflib.SequenceMatcher(a=ref_words, b=rec_words)
    words_out = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():

        if tag == "equal":
            for ri, rj in zip(range(i1, i2), range(j1, j2)):
                score = calculate_word_score(ref_words[ri], rec_words[rj])
                words_out.append({
                    "Word": display_words[rj],
                    "ErrorType": "None" if score >= 60 else "Mispronunciation",
                    "AccuracyScore": score,
                })

        elif tag == "replace":
            n_ref = i2 - i1
            n_rec = j2 - j1
            for k in range(max(n_ref, n_rec)):
                if k < n_ref and k < n_rec:
                    score = calculate_word_score(ref_words[i1+k], rec_words[j1+k])
                    words_out.append({
                        "Word": display_words[j1+k],
                        "ErrorType": "Mispronunciation",
                        "AccuracyScore": score,
                    })
                elif k < n_rec:
                    words_out.append({
                        "Word": display_words[j1+k],
                        "ErrorType": "Insertion",
                        "AccuracyScore": 0,
                    })
                elif k < n_ref:
                    words_out.append({
                        "Word": orig_ref_words[i1+k],
                        "ErrorType": "Omission",
                        "AccuracyScore": 0,
                    })

        elif tag == "delete":
            for ri in range(i1, i2):
                words_out.append({
                    "Word": orig_ref_words[ri],
                    "ErrorType": "Omission",
                    "AccuracyScore": 0,
                })

        elif tag == "insert":
            for rj in range(j1, j2):
                words_out.append({
                    "Word": display_words[rj],
                    "ErrorType": "Insertion",
                    "AccuracyScore": 0,
                })

    scores = [w["AccuracyScore"] for w in words_out]
    accuracy = round(sum(scores) / len(scores), 2) if scores else 0.0

    if rec_starts and rec_ends:
        duration = max(rec_ends) - min(rec_starts)
        wps = len(rec_words) / duration if duration > 0 else 0
        fluency = round(max(0.0, min(100.0, (wps - 0.5) / 4.5 * 100.0)), 2)
    else:
        fluency = 0.0

    pron_score = round(0.8 * accuracy + 0.2 * fluency, 2)
    pron_score = max(pron_score, 20)

    result = {
        "RecognitionStatus": "Success",
        "Transcript": recognized_text,
        "NBest": [{
            "AccuracyScore": accuracy,
            "FluencyScore": fluency,
            "PronScore": pron_score,
            "Words": words_out,
        }],
    }

    AnkiPA.RESULT = result
    return result