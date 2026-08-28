"""Config is the only way to change settings, so bad input must be caught early."""

from pathlib import Path

import pytest

from vi_ai.config import Config, ConfigError, from_dict, load_config


def test_defaults_are_usable_with_no_file():
    config = Config()
    assert config.api.model == "claude-opus-5"
    assert "Return" in config.keys.send
    assert config.source is None


def test_a_partial_file_keeps_the_other_defaults():
    config = from_dict({"ui": {"font_size": 48}})
    assert config.ui.font_size == 48
    assert config.ui.fullscreen is True
    assert config.api.model == "claude-opus-5"


def test_a_single_key_may_be_given_as_a_string():
    config = from_dict({"keys": {"send": "Return"}})
    assert config.keys.send == ["Return"]


def test_unknown_settings_warn_rather_than_fail():
    """A typo should not stop a blind user's machine from starting up."""
    config = from_dict({"api": {"modle": "typo"}, "nonsense": {}})
    assert config.api.model == "claude-opus-5"
    assert len(config.warnings) == 2


def test_a_key_bound_twice_is_reported():
    config = from_dict({"keys": {"send": "F1", "speak_prompt": "F1"}})
    assert any("F1" in warning for warning in config.warnings)


@pytest.mark.parametrize(
    "data,fragment",
    [
        ({"api": {"max_tokens": "lots"}}, "max_tokens"),
        ({"api": {"max_tokens": 0}}, "at least 1"),
        ({"api": {"effort": "turbo"}}, "effort"),
        ({"ui": {"fullscreen": "yes"}}, "true or false"),
        ({"ui": {"font_size": 2}}, "at least 6"),
        ({"keys": {"send": [1, 2]}}, "list of strings"),
        ({"speech": {"engine": "festival"}}, "engine"),
        ({"speech": {"engine": "command"}}, "speech.command"),
        ({"speech": {"engine": "piper"}}, "piper_model"),
        ({"api": "not-a-table"}, "must be a table"),
    ],
)
def test_invalid_values_are_rejected_with_a_readable_message(data, fragment):
    with pytest.raises(ConfigError) as excinfo:
        from_dict(data)
    assert fragment in str(excinfo.value)


def test_effort_can_be_omitted_for_older_models():
    assert from_dict({"api": {"effort": "none"}}).api.effort == "none"


def test_environment_key_wins_over_the_config_file(monkeypatch):
    config = from_dict({"api": {"key": "from-file"}})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config.api_key() == "from-file"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert config.api_key() == "from-env"


def test_a_real_file_round_trips(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[api]\nmodel = "claude-sonnet-5"\n\n[ui]\nfullscreen = false\n')
    config = load_config(path)
    assert config.api.model == "claude-sonnet-5"
    assert config.ui.fullscreen is False
    assert config.source == path


def test_a_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_malformed_toml_reports_the_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[api\nmodel = broken")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert "not valid TOML" in str(excinfo.value)


def test_the_shipped_example_config_is_valid():
    """The example is what users copy, so it must parse and match the defaults."""
    example = Path(__file__).resolve().parents[1] / "config.example.toml"
    config = load_config(example)
    assert config.api.model == Config().api.model
    assert config.warnings == []
