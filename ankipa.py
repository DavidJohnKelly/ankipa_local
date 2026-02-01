from aqt.sound import RecordDialog
from typing import Optional
from aqt import mw
from aqt.qt import Qt
from .stats import get_stat, update_stat, update_avg_stat, save_stats
from .templates.loader import load_template
import threading
import json
import re
import os

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
        from . import app_settings

        to_read = None
        dom_text_extracted = False
        extraction_method = app_settings.value("extraction-method", defaultValue="auto")

        if extraction_method in ["auto", "dom"]:
            try:
                default_selectors = "#sentences-inner .fr, .sentence.fr, .fr.sentence, [data-sentence], .example-sentence"
                selectors = app_settings.value("dom-selectors", defaultValue=default_selectors)

                mw.reviewer.web.eval(f"window.ankipaSetSelectors ? window.ankipaSetSelectors({json.dumps(selectors)}) : null")

                def on_js_result(result):
                    nonlocal to_read, dom_text_extracted
                    if result and isinstance(result, str) and result.strip():
                        to_read = result.strip()
                        dom_text_extracted = True

                mw.reviewer.web.evalWithCallback(
                    "window.ankipaGetVisibleText ? window.ankipaGetVisibleText() : null",
                    on_js_result
                )

                import time
                max_wait = 0.2
                start = time.time()
                while not dom_text_extracted and (time.time() - start) < max_wait:
                    mw.app.processEvents()
                    time.sleep(0.01)

                if dom_text_extracted:
                    mw.reviewer.web.eval("window.ankipaHighlightText ? window.ankipaHighlightText() : null")
            except:
                pass

        if not to_read and extraction_method != "dom":
            if mw.col is None:
                print("Error: mw.col is None")
                return
            if mw.reviewer.card is None:
                print("Error: mw.reviewer.card is None")
                return
            notetype = mw.reviewer.card.note().note_type()
            if notetype is None:
                print("Error: notetype is None")
                return
            field_names = mw.col.models.field_names(notetype)
            fields: str = app_settings.value("fields")
            field_to_use = field_names[0]

            if fields is not None:
                fields_list = fields.replace(" ", "").split(",")
                for field in fields_list:
                    if field in field_names:
                        field_to_use = field
                        break

            to_read = mw.reviewer.card.note()[field_to_use]

            to_read = re.sub(REMOVE_HTML_RE, " ", to_read).replace("&nbsp;", "")

            to_read = re.sub(REMOVE_TAG_RE, "", to_read).strip()

        cls.REFTEXT = to_read

        if mw.reviewer.card is None:
            print("Error: mw.reviewer.card is None")
            return
        cid = mw.reviewer.card.id
        if cls.LAST_TTS != cid:
            cls.TTS_GEN = None
            cls.LAST_TTS = cid

        # Record user voice
        cls.DIAG = RecordDialog(mw, mw, cls.after_record)

    @classmethod
    def after_record(cls, recorded_voice: Optional[str]) -> None:
        if not recorded_voice:
            mw.reviewer.web.eval("window.ankipaRemoveHighlight ? window.ankipaRemoveHighlight() : null")
            return

        if cls.DIAG is None:
            print("Error: AnkiPA.DIAG is None")
            return

        elapsed = cls.DIAG._recorder.duration() - 0.5
        elapsed = round(elapsed, 2)

        cls.RECORDED = recorded_voice

        from . import (
            app_settings,
            data,
            html_template,
            showInfo,
            get_color,
            ResultsDialog,
        )
        from .pronunciation import pron_assess

        region = app_settings.value("region")
        language = app_settings.value("language")
        key = app_settings.value("key")
        if not all((region, language, key)):
            showInfo("Please configure your Azure service properly.")
            mw.reviewer.web.eval("window.ankipaRemoveHighlight ? window.ankipaRemoveHighlight() : null")
            return

        # Perform pronunciation assessment
        lang = data["languages"][language][0]
        phoneme_system = app_settings.value("phoneme-system", defaultValue="IPA")
        timeout = int(app_settings.value("timeout", defaultValue=5))

        t = threading.Thread(
            target=pron_assess,
            args=(
                region,
                lang,
                key,
                cls.REFTEXT,
                recorded_voice,
                phoneme_system,
                timeout,
            ),
        )
        t.start()
        t.join(timeout)

        if cls.RESULT is None or t.is_alive():
            cls.RESULT = None
            mw.reviewer.web.eval("window.ankipaRemoveHighlight ? window.ankipaRemoveHighlight() : null")
            network_error_html = load_template("network_error.html")
            showInfo(
                network_error_html
            )
            return

        if cls.RESULT["RecognitionStatus"] != "Success":
            from . import addon

            mw.reviewer.web.eval("window.ankipaRemoveHighlight ? window.ankipaRemoveHighlight() : null")

            with open(os.path.join(addon, "debug.json"), "w+") as fp:
                data = {}
                data["language"] = lang
                data["region"] = region
                data["text"] = cls.REFTEXT
                data["response"] = cls.RESULT
                json.dump(data, fp, indent=4)

            service_error_html = load_template("service_error.html")
            showInfo(
                service_error_html
            )
            return

        update_stat("assessments", 1)
        update_stat("pronunciation_time", elapsed)

        assessments = get_stat("assessments")

        scores = cls.RESULT["NBest"][0]
        accuracy = scores["AccuracyScore"]
        fluency = scores["FluencyScore"]
        pronunciation = scores["PronScore"]

        update_avg_stat("avg_accuracy", accuracy, assessments)
        update_avg_stat("avg_fluency", fluency, assessments)
        update_avg_stat("avg_pronunciation", pronunciation, assessments)

        words_list = scores.get("Words", [])
        if not isinstance(words_list, list):
            words_list = []
        update_stat("words", len(words_list))
        save_stats()

        # Replace percentages in template
        html = html_template.replace("[ACCURACY]", str(int(accuracy)))
        html = html.replace("[FLUENCY]", str(int(fluency)))
        html = html.replace("[PRONUNCIATION]", str(int(pronunciation)))

        # Replace percentages colors in template
        html = html.replace("[ACCURACY-COLOR]", get_color(accuracy))
        html = html.replace("[FLUENCY-COLOR]", get_color(fluency))
        html = html.replace("[PRONUNCIATION-COLOR]", get_color(pronunciation))

        errors = {"Mispronunciation": 0, "Omission": 0, "Insertion": 0}

        words_html = ""
        for word in words_list:
            syllables = ""
            if "Syllables" in word:
                syllable_count = len(word["Syllables"])
                for i, syllable in enumerate(word["Syllables"]):
                    syllable_score = syllable["AccuracyScore"]
                    add = " &#x2022; " if i < (syllable_count - 1) else ""
                    syllables += (
                        f"<span style='color: {get_color(syllable_score)};'>"
                        + f"{syllable['Syllable']}</span>"
                        + f"<span style='color: white;'>{add}</span>"
                    )

            error = word["ErrorType"]
            words_html += (
                WORD_HTML.replace("[WORD]", word["Word"])
                .replace("[SYLLABLES]", syllables)
                .replace("[ERROR]", error)
                .replace("[ERROR-INFO]", error if error != "None" else "Correct")
            )
            if error != "None":
                errors[error] += 1

        # Replace wordlist
        html = html.replace("[WORDLIST]", words_html)

        # Replace errors count
        html = html.replace("[MISPRONUNCIATIONS]", str(errors["Mispronunciation"]))
        html = html.replace("[OMISSIONS]", str(errors["Omission"]))
        html = html.replace("[INSERTIONS]", str(errors["Insertion"]))

        cls.RESULT = None

        mw.reviewer.web.eval("window.ankipaRemoveHighlight ? window.ankipaRemoveHighlight() : null")

        widget = ResultsDialog(html, pronunciation)
        widget.setWindowModality(Qt.WindowModality.NonModal)
        widget.show()
