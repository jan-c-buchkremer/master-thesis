from Config import Config


def test_config_reads_env_overrides():
    # conftest sets FLASK_PORT=5055 and DEBUG=no before Config is imported
    assert Config.PORT == 5055
    assert isinstance(Config.PORT, int)
    assert Config.DEBUG is False
