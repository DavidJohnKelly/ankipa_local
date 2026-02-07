# pronunciation.py
import json
import wave
import os
import tempfile
import audioop
import difflib

from .ankipa import AnkiPA
from vosk import Model, KaldiRecognizer
from g2p_en import G2p
from rapidfuzz import fuzz


MODEL_PATH = os.path.join(
    os.path.dirname(__file__),
    "models",
    "vosk-model-small-en-us-0.15",
)

_VOSK_MODEL = None


def _ensure_wav_16k_mono(src_path: str) -> str:
    """Return path to a WAV file that is 16kHz mono 16-bit PCM."""
    with wave.open(src_path, "rb") as w:
        nchannels, sampwidth, framerate, nframes, _, _ = w.getparams()
        frames = w.readframes(nframes)

    if nchannels > 1:
        frames = audioop.tomono(frames, sampwidth, 1, 0)

    if framerate != 16000:
        frames, _ = audioop.ratecv(frames, sampwidth, 1, framerate, 16000, None)

    if sampwidth != 2:
        frames = audioop.lin2lin(frames, sampwidth, 2)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp_path = tmp.name
    tmp.close()

    with wave.open(tmp_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(frames)

    return tmp_path


def _tokenize(text: str):
    return [
        t for t in "".join(
            c if c.isalnum() or c.isspace() else " " for c in text
        ).split()
        if t
    ]


def pron_assess(reference_text, recorded_voice):
    """
    Local pronunciation assessment (Vosk + g2p_en + rapidfuzz).
    Azure parameters are ignored but kept for compatibility.
    """

    if Model is None or G2p is None or fuzz is None:
        AnkiPA.RESULT = None
        return

    if not os.path.isdir(MODEL_PATH):
        print(f"Vosk model not found: {MODEL_PATH}")
        AnkiPA.RESULT = None
        return

    global _VOSK_MODEL
    if _VOSK_MODEL is None:
        _VOSK_MODEL = Model(MODEL_PATH)

    try:
        wav_path = _ensure_wav_16k_mono(recorded_voice)
        rec = KaldiRecognizer(_VOSK_MODEL, 16000.0)
        rec.SetWords(True)

        recognized = []
        with wave.open(wav_path, "rb") as wf:
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    recognized.extend(json.loads(rec.Result()).get("result", []))
            recognized.extend(json.loads(rec.FinalResult()).get("result", []))

    except Exception as e:
        print(f"Local pronunciation failed: {e}")
        AnkiPA.RESULT = None
        return
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass

    g2p = G2p()
    ref_words = _tokenize(reference_text)
    ref_phones = {
        w: " ".join(p for p in g2p(w) if p.strip())
        for w in ref_words
    }

    rec_words = [r["word"] for r in recognized]
    rec_starts = [r.get("start", 0.0) for r in recognized]
    rec_ends = [r.get("end", 0.0) for r in recognized]

    sm = difflib.SequenceMatcher(a=ref_words, b=rec_words)
    words_out = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for ri, rj in zip(range(i1, i2), range(j1, j2)):
                ref = ref_words[ri]
                rec = rec_words[rj]
                phone_sim = fuzz.ratio(ref_phones[ref], " ".join(g2p(rec)))
                orth_sim = fuzz.ratio(ref.lower(), rec.lower())
                score = int(round(0.6 * orth_sim + 0.4 * phone_sim))
                words_out.append({
                    "Word": rec,
                    "ErrorType": "None" if score >= 60 else "Mispronunciation",
                    "AccuracyScore": score,
                    "Syllables": [],
                })

        elif tag == "replace":
            for rj in range(j1, j2):
                words_out.append({
                    "Word": rec_words[rj],
                    "ErrorType": "Mispronunciation",
                    "AccuracyScore": 0,
                    "Syllables": [],
                })

        elif tag == "delete":
            for ri in range(i1, i2):
                words_out.append({
                    "Word": ref_words[ri],
                    "ErrorType": "Omission",
                    "AccuracyScore": 0,
                    "Syllables": [],
                })

        elif tag == "insert":
            for rj in range(j1, j2):
                words_out.append({
                    "Word": rec_words[rj],
                    "ErrorType": "Insertion",
                    "AccuracyScore": 0,
                    "Syllables": [],
                })

    scores = [w["AccuracyScore"] for w in words_out]
    accuracy = round(sum(scores) / len(scores), 2) if scores else 0.0

    if rec_starts and rec_ends:
        duration = max(rec_ends) - min(rec_starts)
        wps = len(rec_words) / duration if duration > 0 else 0
        fluency = round(max(0.0, min(100.0, (wps - 0.5) / 4.5 * 100.0)), 2)
    else:
        fluency = 0.0

    pron_score = round(0.7 * accuracy + 0.3 * fluency, 2)

    AnkiPA.RESULT = {
        "RecognitionStatus": "Success",
        "NBest": [{
            "AccuracyScore": accuracy,
            "FluencyScore": fluency,
            "PronScore": pron_score,
            "Words": words_out,
        }],
    }
