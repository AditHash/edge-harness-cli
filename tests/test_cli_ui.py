def test_blank_text_draws_no_empty_bullet() -> None:
    from code_harness.cli import _ReplyView

    view = _ReplyView()
    view.add(" ")
    view.add("\n")
    assert view.live is None
    view.stop()
    view.add("\n")
    view.add("Answer")
    assert view.live is not None and "Answer" in view.text
    view.stop()
