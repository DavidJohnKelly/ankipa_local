from aqt.sound import RecordDialog
from typing import Optional
from aqt import mw
from aqt.qt import Qt
from .stats import update_stat, update_avg_stat, save_stats
from .templates.loader import load_template
import threading
import re

REMOVE_HTML_RE = re.compile(r"<[^<]+?>")
REMOVE_TAG_RE = re.compile(r"\[[^\]]+\]")
WORD_HTML: str = load_template("word.html")


class AnkiPA:
    REFTEXT: Optional[str] = None
    RECORDED: Optional[str] = None
    TTS_GEN: Optional[str] = None
    LAST_TTS: Optional[int] = None
    RESULT: Optional[dict] = None
    DIAG: Optional[RecordDialog] = None

    @classmethod
    def test_pronunciation(cls):
        to_read = None

        if mw.reviewer.card is None:
            return

        field = mw.col.models.field_names(
            mw.reviewer.card.note().note_type()
        )[0]

        to_read = mw.reviewer.card.note()[field]
        to_read = re.sub(REMOVE_HTML_RE, " ", to_read)
        to_read = re.sub(REMOVE_TAG_RE, "", to_read).strip()

        cls.REFTEXT = to_read
        cls.DIAG = RecordDialog(mw, mw, cls.after_record)

    @classmethod
    def after_record(cls, recorded_voice: Optional[str]) -> None:
        from . import ResultsDialog

        if not recorded_voice:
            return

        cls.RECORDED = recorded_voice
        cls.RESULT = None

        from .pronunciation import pron_assess

        t = threading.Thread(
            target=pron_assess,
            args=(None, None, None, cls.REFTEXT, recorded_voice, None, None),
        )
        t.start()
        t.join()

        if cls.RESULT is None:
            show_html = load_template("network_error.html")
            mw.taskman.run_on_main(lambda: mw.reviewer.web.eval(show_html))
            return

        scores = cls.RESULT["NBest"][0]
        accuracy = scores["AccuracyScore"]
        fluency = scores["FluencyScore"]
        pronunciation = scores["PronScore"]

        update_stat("assessments", 1)
        update_avg_stat("avg_accuracy", accuracy, 1)
        update_avg_stat("avg_fluency", fluency, 1)
        update_avg_stat("avg_pronunciation", pronunciation, 1)
        save_stats()

        html = load_template("result.html")
        html = html.replace("[ACCURACY]", str(int(accuracy)))
        html = html.replace("[FLUENCY]", str(int(fluency)))
        html = html.replace("[PRONUNCIATION]", str(int(pronunciation)))

        widget = ResultsDialog(html, pronunciation)
        widget.setWindowModality(Qt.WindowModality.NonModal)
        widget.show()
