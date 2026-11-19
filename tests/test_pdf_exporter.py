"""The PDF of a PDMS proposal: made by Word when it can be, else by LibreOffice. Nothing here starts Word or
LibreOffice: the two engines are replaced by stand-ins; the real Word run is a manual check."""
import base64
import os

import harness as H
from ctr_tools import pdf_exporter as P


def _stub(monkey: dict):
    saved = {k: getattr(P, k) for k in monkey}
    for k, v in monkey.items():
        setattr(P, k, v)
    return saved


def _restore(saved):
    for k, v in saved.items():
        setattr(P, k, v)


def test_word_is_used_when_it_works_and_libreoffice_when_it_does_not():
    calls = []
    saved = _stub({"export_docx_via_word": lambda d, o, *a, **k: (calls.append("word"), P.Path(o) / "a.pdf")[1],
                   "export_to_pdf": lambda d, o: (calls.append("lo"), P.Path(o) / "a.pdf")[1]})
    prev = os.environ.pop("CTR_PDF_ENGINE", None)
    try:
        assert P.export_docx_to_pdf("a.docx", "out")[1] == "Word" and calls == ["word"]
        def broken(*a, **k):
            raise RuntimeError("Word is not installed")
        P.export_docx_via_word = broken
        calls.clear()
        assert P.export_docx_to_pdf("a.docx", "out")[1] == "LibreOffice" and calls == ["lo"]      # fell back
        P.export_docx_via_word = lambda *a, **k: (calls.append("word"), None)[1]
        calls.clear()
        os.environ["CTR_PDF_ENGINE"] = "libreoffice"                                              # forced
        assert P.export_docx_to_pdf("a.docx", "out")[1] == "LibreOffice" and calls == ["lo"]
    finally:
        _restore(saved)
        os.environ.pop("CTR_PDF_ENGINE", None)
        if prev is not None:
            os.environ["CTR_PDF_ENGINE"] = prev
        else:
            os.environ["CTR_PDF_ENGINE"] = "libreoffice"


def test_without_powershell_word_is_not_tried_and_the_script_is_safe():
    saved = _stub({"_powershell": lambda: None})
    try:
        try:
            P.export_docx_via_word("a.docx", "out")
        except RuntimeError as exc:
            assert "PowerShell" in str(exc)
        else:
            raise AssertionError("Word was tried without PowerShell")
    finally:
        _restore(saved)
    script = P._word_script("C:\\Users\\O'Neil\\a ₼.docx", "C:\\out\\a.pdf")
    assert "'C:\\Users\\O''Neil\\a ₼.docx'" in script                                             # a quote in a path is escaped
    assert "ExportAsFixedFormat('C:\\out\\a.pdf', 17)" in script and "UpdatePageNumbers()" in script
    assert "$d.Close([ref]0)" in script and "Documents.Count -eq 0" in script                      # no save; never quits another Word's windows
    assert "Documents.Open(" in script and "$false, $true)" in script                              # read-only, no conversion dialog
    base64.b64encode(script.encode("utf-16-le"))                                                    # encodable as -EncodedCommand


def test_after_a_timeout_only_the_word_this_run_started_is_stopped():
    cmds = []
    saved_run, saved_sleep = P.subprocess.run, P.time.sleep
    P.subprocess.run = lambda cmd, *a, **k: cmds.append(cmd)
    P.time.sleep = lambda s: None
    try:
        P._stop_started_word("WORD-BEFORE: 3676,5544\n", "powershell.exe")
        assert len(cmds) == 1
        ps_cmd = cmds[0][-1]
        assert "$b = @(3676,5544)" in ps_cmd and "$b -notcontains $_.Id" in ps_cmd      # only a process that is new …
        assert "MainWindowHandle -eq 0" in ps_cmd                                         # … and has no window (ours is invisible)
        cmds.clear()
        P._stop_started_word("", "powershell.exe")                                        # it never got as far as printing: nothing to stop
        P._stop_started_word("something else\n", "powershell.exe")
        assert cmds == []
    finally:
        P.subprocess.run, P.time.sleep = saved_run, saved_sleep
    assert "WORD-BEFORE:" in P._word_script("a", "b") and "Word.Application" in P._word_script("a", "b")
