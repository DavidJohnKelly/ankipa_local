import json
import wave
import os
import tempfile
import difflib

import numpy as np

from vosk import Model, KaldiRecognizer
from g2p_en import G2p
from rapidfuzz import fuzz
from scipy import signal

from .ankipa import AnkiPA


MODEL_PATH = os.path.join(
    os.path.dirname(__file__),
    "models",
    "vosk-model-small-en-us-0.15",
)

_VOSK_MODEL = None
_G2P = None


def _ensure_wav_16k_mono(src_path: str) -> str:
    """Return path to a WAV file that is 16kHz mono 16-bit PCM."""
    with wave.open(src_path, "rb") as w:
        nchannels, sampwidth, framerate, nframes, _, _ = w.getparams()
        frames = w.readframes(nframes)

    # Convert bytes to numpy array
    audio_data = np.frombuffer(frames, dtype=np.int16 if sampwidth == 2 else np.int8)
    
    # Convert to mono if stereo
    if nchannels > 1:
        audio_data = audio_data.reshape(-1, nchannels)
        audio_data = np.mean(audio_data, axis=1).astype(np.int16)
    
    # Resample to 16kHz if needed
    if framerate != 16000:
        num_samples = int(len(audio_data) * 16000 / framerate)
        audio_data = signal.resample(audio_data, num_samples).astype(np.int16)
    
    # Ensure 16-bit (2 bytes per sample)
    if sampwidth != 2:
        if sampwidth == 1:
            # 8-bit to 16-bit
            audio_data = (audio_data.astype(np.int16) - 128) * 256
        else:
            # Adjust to 16-bit
            bits_needed = 16 - (sampwidth * 8)
            if bits_needed > 0:
                audio_data = audio_data.astype(np.int16) << bits_needed
            else:
                audio_data = (audio_data.astype(np.int32) >> -bits_needed).astype(np.int16)

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
    """
    Local pronunciation assessment using:
    - Vosk for speech recognition
    - grapheme to phoneme conversion for better matching
    - phoneme edit distance scoring
    """

    if not os.path.isdir(MODEL_PATH):
        print(f"Vosk model not found: {MODEL_PATH}")
        AnkiPA.RESULT = None
        return

    global _VOSK_MODEL
    if _VOSK_MODEL is None:
        _VOSK_MODEL = Model(MODEL_PATH)

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
        print(f"Local pronunciation failed: {e}")
        AnkiPA.RESULT = None
        return
    finally:
        try:
            if wav_path and os.path.exists(wav_path):
                os.unlink(wav_path)
        except Exception:
            print("Failed to clean up temporary WAV file")
            pass

    global _G2P
    if _G2P is None:
        _G2P = G2p()

    ref_words = _tokenise(reference_text)
    rec_words = [r["word"] for r in recognised]
    recognized_text = " ".join(rec_words)

    rec_starts = [r.get("start", 0.0) for r in recognised]
    rec_ends = [r.get("end", 0.0) for r in recognised]

    ref_phones = {
        w: [p for p in _G2P(w) if p.strip()]
        for w in ref_words
    }

    sm = difflib.SequenceMatcher(a=ref_words, b=rec_words)

    words_out = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for ri, rj in zip(range(i1, i2), range(j1, j2)):
                ref = ref_words[ri]
                rec = rec_words[rj]

                ref_ph = ref_phones.get(ref, [])
                rec_ph = [p for p in _G2P(rec) if p.strip()]

                # Phoneme edit distance
                if ref_ph or rec_ph:
                    dist = sum(
                        1 for a, b in zip(ref_ph, rec_ph) if a != b
                    ) + abs(len(ref_ph) - len(rec_ph))

                    max_len = max(len(ref_ph), len(rec_ph))
                    phone_sim = 100 * (1 - dist / max_len) if max_len else 0
                else:
                    phone_sim = 0

                orth_sim = fuzz.ratio(ref.lower(), rec.lower())
                score = int(round(0.7 * phone_sim + 0.3 * orth_sim))
                words_out.append({
                    "Word": rec,
                    "ErrorType": "None" if score >= 60 else "Mispronunciation",
                    "AccuracyScore": score,
                })

        elif tag == "replace":
            for rj in range(j1, j2):
                words_out.append({
                    "Word": rec_words[rj],
                    "ErrorType": "Mispronunciation",
                    "AccuracyScore": 0,
                })

        elif tag == "delete":
            for ri in range(i1, i2):
                words_out.append({
                    "Word": ref_words[ri],
                    "ErrorType": "Omission",
                    "AccuracyScore": 0,
                })

        elif tag == "insert":
            for rj in range(j1, j2):
                words_out.append({
                    "Word": rec_words[rj],
                    "ErrorType": "Insertion",
                    "AccuracyScore": 0,
                })

    scores = [w["AccuracyScore"] for w in words_out]

    accuracy = round(sum(scores) / len(scores), 2) if scores else 0.0

    # fluency estimate from speaking rate
    if rec_starts and rec_ends:
        duration = max(rec_ends) - min(rec_starts)
        wps = len(rec_words) / duration if duration > 0 else 0
        fluency = round(max(0.0, min(100.0, (wps - 0.5) / 4.5 * 100.0)), 2)
    else:
        fluency = 0.0

    pron_score = round(0.8 * accuracy + 0.2 * fluency, 2)
    pron_score = max(pron_score, 20) # min score of 20 to prevent demoralisation

    AnkiPA.RESULT = {
        "RecognitionStatus": "Success",
        "Transcript": recognized_text,
        "NBest": [{
            "AccuracyScore": accuracy,
            "FluencyScore": fluency,
            "PronScore": pron_score,
            "Words": words_out,
        }],
    }
