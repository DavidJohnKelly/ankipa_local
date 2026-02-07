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

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "vosk-model-small-en-us-0.15")  # set to your bundled model


def _ensure_wav_16k_mono(src_path: str) -> str:
    """Return path to a WAV file that is 16kHz mono 16-bit PCM."""
    with wave.open(src_path, "rb") as w:
        nchannels, sampwidth, framerate, nframes, comptype, compname = w.getparams()
        frames = w.readframes(nframes)

    # Convert to mono if needed
    if nchannels > 1:
        frames = audioop.tomono(frames, sampwidth, 1, 0)

    # Resample if needed
    if framerate != 16000:
        frames, _ = audioop.ratecv(frames, sampwidth, 1, framerate, 16000, None)

    # Ensure 16-bit width
    if sampwidth != 2:
        # audioop.lin2lin to convert sample width
        frames = audioop.lin2lin(frames, sampwidth, 2)
        sampwidth = 2

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
    # simple tokenization; improve as needed
    return [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in text).split() if t]


def pron_assess(region, lang, key, reference_text, recorded_voice, phoneme_system, timeout):
    """
    Local pronunciation assessment (VOSK + g2p_en + rapidfuzz).
    Writes to AnkiPA.RESULT similar to Azure response shape.
    """
    # Fail early if deps not available
    if Model is None or G2p is None or fuzz is None:
        print("Pronunciation local assessment dependencies missing.")
        AnkiPA.RESULT = None
        return

    if not os.path.isdir(MODEL_PATH):
        print(f"VOSK model not found at {MODEL_PATH}")
        AnkiPA.RESULT = None
        return

    # Prepare audio
    try:
        wav_path = _ensure_wav_16k_mono(recorded_voice)
    except Exception as e:
        print(f"Audio conversion failed: {e}")
        AnkiPA.RESULT = None
        return

    try:
        model = Model(MODEL_PATH)
        rec = KaldiRecognizer(model, 16000.0)
        rec.SetWords(True)
    except Exception as e:
        print(f"VOSK model init failed: {e}")
        AnkiPA.RESULT = None
        return

    # Run recognizer
    recognized_words = []
    with wave.open(wav_path, "rb") as wf:
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                for w in res.get("result", []):
                    recognized_words.append(w)
        # final
        final = json.loads(rec.FinalResult())
        for w in final.get("result", []):
            recognized_words.append(w)

    # remove temporary file
    try:
        os.unlink(wav_path)
    except Exception:
        pass

    # Tokenize/reference phonemes
    g2p = G2p()
    ref_words = _tokenize(reference_text)
    ref_phones = {w: " ".join([p for p in g2p(w) if p.strip()]) for w in ref_words}

    rec_words = [r["word"] for r in recognized_words]
    rec_confs = [r.get("conf", 1.0) for r in recognized_words]
    rec_starts = [r.get("start", 0.0) for r in recognized_words]
    rec_ends = [r.get("end", 0.0) for r in recognized_words]

    # Align sequences (simple token alignment)
    sm = difflib.SequenceMatcher(a=ref_words, b=rec_words)
    words_out = []
    # We'll iterate opcodes to produce word entries corresponding to recognized words (and insertions/omissions)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for ri, rj in zip(range(i1, i2), range(j1, j2)):
                ref = ref_words[ri]
                rec = rec_words[rj]
                conf = float(rec_confs[rj]) * 100.0  # 0-100
                # phone similarity
                rec_phones = " ".join([p for p in g2p(rec) if p.strip()])
                phone_sim = fuzz.ratio(ref_phones[ref], rec_phones)
                orth_sim = fuzz.ratio(ref.lower(), rec.lower())
                word_score = int(round(0.6 * orth_sim + 0.4 * phone_sim))
                error_type = "None" if word_score >= 60 else "Mispronunciation"
                words_out.append(
                    {
                        "Word": rec,
                        "ErrorType": error_type,
                        "AccuracyScore": word_score,
                        "Syllables": [],  # optional: derive from phones later
                    }
                )
        elif tag == "replace":
            # substitutions -> mark as mispronunciation
            for rj in range(j1, j2):
                rec = rec_words[rj]
                rec_phones = " ".join([p for p in g2p(rec) if p.strip()])
                # take nearest reference chunk to compute similarity (use middle ref word)
                mid_ref = ref_words[i1] if i1 < len(ref_words) else ""
                phone_sim = fuzz.ratio(ref_phones.get(mid_ref, ""), rec_phones)
                orth_sim = fuzz.ratio(mid_ref.lower(), rec.lower()) if mid_ref else 0
                score = int(round(0.6 * orth_sim + 0.4 * phone_sim))
                words_out.append(
                    {
                        "Word": rec,
                        "ErrorType": "Mispronunciation",
                        "AccuracyScore": max(0, score),
                        "Syllables": [],
                    }
                )
            # omissions (if any ref words deleted)
            # we won't add explicit omission entries in 'Words' (Azure returns recognized words)
        elif tag == "delete":
            # reference words deleted => omissions; add an entry to reflect omission (no recognized token)
            for ri in range(i1, i2):
                words_out.append(
                    {"Word": ref_words[ri], "ErrorType": "Omission", "AccuracyScore": 0, "Syllables": []}
                )
        elif tag == "insert":
            # extra recognized words => insertions
            for rj in range(j1, j2):
                rec = rec_words[rj]
                words_out.append(
                    {"Word": rec, "ErrorType": "Insertion", "AccuracyScore": 0, "Syllables": []}
                )

    # Compute global scores
    per_word_scores = [w["AccuracyScore"] for w in words_out if isinstance(w.get("AccuracyScore"), (int, float))]
    accuracy = round(sum(per_word_scores) / len(per_word_scores), 2) if per_word_scores else 0.0

    # Fluency: word rate normalized
    if rec_starts and rec_ends:
        duration = max(rec_ends) - min(rec_starts)
        word_count = len([w for w in words_out if w["ErrorType"] != "Omission"])
        wps = word_count / duration if duration > 0 else 0
        # Map wps (words per second) into 0..100 range; typical 0.5 - 5 wps -> map accordingly
        fluency = max(0.0, min(100.0, (wps - 0.5) / (4.5) * 100.0))
        fluency = round(fluency, 2)
    else:
        fluency = 0.0

    pron_score = round(0.7 * accuracy + 0.3 * fluency, 2)

    # Compose RESULT similar to Azure's shape
    AnkiPA.RESULT = {
        "RecognitionStatus": "Success",
        "NBest": [
            {
                "AccuracyScore": accuracy,
                "FluencyScore": fluency,
                "PronScore": pron_score,
                "Words": words_out,
            }
        ],
    }