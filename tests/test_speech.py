"""Engine selection, the speech queue, and the two bugs found in real use."""

import shutil
import time

import pytest

from vi_ai.config import from_dict
from vi_ai.speech import (
    Category,
    CommandEngine,
    EspeakEngine,
    NullEngine,
    SpdSayEngine,
    SpeechManager,
    _SubprocessEngine,
    build_engine,
)


def test_a_missing_engine_degrades_to_silence_rather_than_crashing(monkeypatch):
    """Exiting because espeak is absent would leave the user with nothing."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    engine = build_engine(from_dict({"speech": {"engine": "auto"}}).speech)
    assert isinstance(engine, NullEngine)
    assert "not found" in engine.describe()


def test_auto_detection_prefers_espeak_ng(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    engine = build_engine(from_dict({"speech": {"engine": "auto"}}).speech)
    assert isinstance(engine, EspeakEngine)
    assert engine.argv[0] == "/usr/bin/espeak-ng"


def test_spd_say_can_be_selected_explicitly(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    engine = build_engine(from_dict({"speech": {"engine": "spd-say"}}).speech)
    assert isinstance(engine, SpdSayEngine)
    # A client/server engine keeps talking after the client dies.
    assert engine.cancel_argv == ["/usr/bin/spd-say", "-C"]


def test_espeak_arguments_carry_the_configured_voice_settings(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    config = from_dict({"speech": {"rate": 300, "voice": "en-gb", "pitch": 20}})
    engine = build_engine(config.speech)
    assert "300" in engine.argv and "en-gb" in engine.argv and "20" in engine.argv


def test_out_of_range_values_are_clamped_not_passed_through(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    engine = build_engine(from_dict({"speech": {"rate": 9000}}).speech)
    assert "450" in engine.argv, "espeak rejects a rate above its maximum"


def test_command_engine_substitutes_text_into_arguments():
    engine = CommandEngine(["say", "-v", "{voice}", "{text}"], "alex", 200, 100, 50)
    argv, stdin = engine._argv_for("hello")
    assert argv == ["say", "-v", "alex", "hello"]
    assert stdin is None


def test_command_engine_uses_stdin_when_text_is_not_in_the_arguments():
    """This is the shape piped engines such as piper need."""
    engine = CommandEngine(["piper", "--model", "x.onnx"], "", 170, 100, 50)
    argv, stdin = engine._argv_for("hello")
    assert argv == ["piper", "--model", "x.onnx"]
    assert stdin == "hello"


def test_speech_is_spoken_in_order():
    engine = NullEngine()
    manager = SpeechManager(engine)
    try:
        manager.speak("one")
        manager.speak("two")
        assert manager.wait_until_idle(2.0)
        assert engine.spoken == ["one", "two"]
    finally:
        manager.shutdown()


def test_interrupting_drops_what_was_queued():
    engine = NullEngine()
    manager = SpeechManager(engine)
    try:
        manager.speak("stale one")
        manager.speak("stale two")
        manager.speak("urgent", interrupt=True)
        assert manager.wait_until_idle(2.0)
        assert engine.spoken[-1] == "urgent"
        assert "stale two" not in engine.spoken
    finally:
        manager.shutdown()


def test_blank_text_is_never_sent_to_the_engine():
    engine = NullEngine()
    manager = SpeechManager(engine)
    try:
        manager.speak("")
        manager.speak("   \n ")
        assert manager.wait_until_idle(1.0)
        assert engine.spoken == []
    finally:
        manager.shutdown()


def test_a_running_process_is_killed_promptly():
    """A long answer must stop the instant the user presses a key."""
    config = from_dict({"speech": {"engine": "command", "command": ["sleep", "10"]}})
    manager = SpeechManager(build_engine(config.speech))
    try:
        manager.speak("long")
        time.sleep(0.3)
        started = time.monotonic()
        manager.stop()
        assert time.monotonic() - started < 1.5
        assert manager.wait_until_idle(2.0)
    finally:
        manager.shutdown()


def test_speak_after_shutdown_is_ignored():
    engine = NullEngine()
    manager = SpeechManager(engine)
    manager.shutdown()
    manager.speak("too late")
    assert engine.spoken == []


def test_interrupting_does_not_block_the_caller_on_a_stubborn_process():
    """`speak(interrupt=True)` runs on the UI thread on every keystroke.

    Waiting there for a process to die would stutter the user's typing, so the
    teardown happens on a separate thread. This engine ignores SIGTERM, which
    forces the slow terminate-then-kill path.
    """
    engine = _SubprocessEngine(["sh", "-c", 'trap "" TERM; sleep 10'])
    manager = SpeechManager(engine)
    try:
        manager.speak("first")
        time.sleep(0.3)
        started = time.monotonic()
        manager.speak("key echo", interrupt=True)
        assert time.monotonic() - started < 0.1, (
            "the caller waited for the process teardown"
        )
    finally:
        manager.shutdown()


def test_a_slow_cancel_command_does_not_block_the_caller():
    """speech-dispatcher needs an external cancel, which can be slow to run."""
    engine = _SubprocessEngine(["sleep", "10"], cancel_argv=["sleep", "2"])
    manager = SpeechManager(engine)
    try:
        manager.speak("first")
        time.sleep(0.2)
        started = time.monotonic()
        manager.stop()
        assert time.monotonic() - started < 0.1
    finally:
        manager.shutdown()


def test_utterances_do_not_overlap_after_an_interruption():
    """Two voices talking at once is worse than either one alone."""
    engine = _SubprocessEngine(["sleep", "0.5"])
    manager = SpeechManager(engine)
    try:
        manager.speak("first")
        time.sleep(0.2)
        manager.speak("second", interrupt=True)
        time.sleep(0.15)
        with manager._lock:
            running = manager._current_utterance
        # The replacement may only start once the first process has exited.
        assert running is None or running._proc.poll() is None
        assert manager.wait_until_idle(3.0)
    finally:
        manager.shutdown()


# -- key echo must survive typing ----------------------------------------
#
# Reported from real use: "It does not speak the letters as they are pressed."
# A key name takes longer to say than it takes to press the next key, so when
# every keystroke cancelled the previous one, typing produced near silence.


def test_key_names_do_not_cancel_each_other():
    engine = NullEngine()
    # A generous backlog, so this tests cancellation and not the backlog cap.
    manager = SpeechManager(engine, max_pending_echoes=50)
    try:
        for letter in "hello":
            manager.speak(letter, category=Category.ECHO)
        assert manager.wait_until_idle(5.0)
        assert engine.spoken == list("hello"), (
            "every key pressed must be heard, not just the last one"
        )
    finally:
        manager.shutdown()


def test_a_key_name_still_stops_an_answer_being_read():
    """The other half of the rule: typing must interrupt a long answer."""
    engine = NullEngine()
    manager = SpeechManager(engine)
    try:
        manager.speak("a very long answer being read out")
        manager.speak("another sentence of it")
        manager.speak("k", category=Category.ECHO)
        assert manager.wait_until_idle(5.0)
        assert "another sentence of it" not in engine.spoken
        assert engine.spoken[-1] == "k"
    finally:
        manager.shutdown()


def test_the_echo_backlog_is_capped_so_speech_stays_near_the_typing():
    """Typing faster than the voice must not leave the user seconds behind."""
    engine = NullEngine()
    manager = SpeechManager(engine, max_pending_echoes=3)
    try:
        with manager._lock:  # hold the worker off so a backlog can build
            for letter in "abcdefgh":
                manager._queue.append(_queued_echo(letter))
            manager._trim_echoes_locked()
            queued = [item.text for item in manager._queue]
        assert len(queued) == 3
        assert queued == ["f", "g", "h"], "the most recent keys are the ones kept"
    finally:
        manager.shutdown()


def _queued_echo(text):
    from vi_ai.speech import _Item
    return _Item(text, Category.ECHO)


def test_an_explicit_interrupt_still_clears_everything():
    engine = NullEngine()
    manager = SpeechManager(engine)
    try:
        manager.speak("a", category=Category.ECHO)
        manager.speak("b", category=Category.ECHO)
        manager.speak("stop everything", interrupt=True)
        assert manager.wait_until_idle(5.0)
        assert engine.spoken[-1] == "stop everything"
    finally:
        manager.shutdown()


# -- the clipped last word ------------------------------------------------
#
# Reported from real use: "It does not fully speak the last word."


def test_espeak_terminates_its_input_with_a_newline():
    """Without it espeak-ng clips the final consonant of the last word."""
    engine = EspeakEngine("espeak-ng", "en", 170, 100, 50)
    _argv, stdin = engine._argv_for("backspace")
    assert stdin == "backspace\n"


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng not installed")
def test_espeak_output_is_not_clipped(tmp_path):
    """The real check: our command must produce the same audio as espeak's own."""
    import subprocess
    import wave

    engine = EspeakEngine(shutil.which("espeak-ng"), "en", 170, 100, 50)
    text = "the capital of france is paris"

    argv, stdin = engine._argv_for(text)
    ours = tmp_path / "ours.wav"
    subprocess.run([*argv, "-w", str(ours)], input=stdin, text=True, check=True)

    reference = tmp_path / "ref.wav"
    subprocess.run(
        ["espeak-ng", "-v", "en", "-s", "170", "-w", str(reference), text], check=True
    )

    with wave.open(str(ours)) as a, wave.open(str(reference)) as b:
        assert a.getnframes() == b.getnframes(), "the tail of the last word was lost"


# -- piper: the higher quality voice --------------------------------------


def test_silence_is_trimmed_from_the_ends():
    """Piper pads each phrase; on a single letter that padding is a third of it."""
    import array

    from vi_ai.speech import _trim_silence

    rate = 22050
    quiet = array.array("h", [0] * rate)          # 1s of silence
    loud = array.array("h", [8000, -8000] * 1000)  # a burst of sound
    padded = (quiet + loud + quiet).tobytes()

    trimmed = _trim_silence(padded, rate)
    assert len(trimmed) < len(padded)
    # The sound itself survives, plus a little padding either side.
    assert len(trimmed) >= len(loud.tobytes())


def test_trimming_leaves_all_silent_audio_alone():
    """A silent clip has no sound to keep, so it must not be emptied."""
    import array

    from vi_ai.speech import _trim_silence

    silence = array.array("h", [0] * 1000).tobytes()
    assert _trim_silence(silence, 22050) == silence


def test_piper_is_chosen_when_a_model_is_configured(tmp_path, monkeypatch):
    from vi_ai.speech import PiperEngine

    model = tmp_path / "voice.onnx"
    model.write_bytes(b"not a real model")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    engine = build_engine(
        from_dict({"speech": {"engine": "piper", "piper_model": str(model)}}).speech
    )
    assert isinstance(engine, PiperEngine)


def test_auto_prefers_piper_over_espeak_when_a_model_is_present(tmp_path, monkeypatch):
    """Piper sounds better and is quicker per phrase, so it wins when available."""
    from vi_ai.speech import PiperEngine

    model = tmp_path / "voice.onnx"
    model.write_bytes(b"not a real model")
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    engine = build_engine(
        from_dict({"speech": {"engine": "auto", "piper_model": str(model)}}).speech
    )
    assert isinstance(engine, PiperEngine)


def test_a_missing_piper_model_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    engine = build_engine(
        from_dict({"speech": {"engine": "auto", "piper_model": "/nope/x.onnx"}}).speech
    )
    assert isinstance(engine, EspeakEngine), "auto must still find a working voice"

    engine = build_engine(
        from_dict({"speech": {"engine": "piper", "piper_model": "/nope/x.onnx"}}).speech
    )
    assert isinstance(engine, NullEngine)
    assert "not found" in engine.describe()


def test_a_player_is_detected_for_piper(monkeypatch):
    from vi_ai.speech import detect_player

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    assert detect_player()[0] == "/usr/bin/aplay", "aplay is cheapest and Pi default"
    assert detect_player(["mine", "-x"]) == ["mine", "-x"], "config must win"

    monkeypatch.setattr("shutil.which", lambda name: None)
    assert detect_player() == []
