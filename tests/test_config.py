import pytest

from fantasy_dash.config import ConfigError, get_credentials


def test_missing_credentials_name_the_settings():
    with pytest.raises(ConfigError, match="ESPN_S2 and SWID"):
        get_credentials("", "")


def test_swid_gets_its_braces_and_values_stay_hidden():
    creds = get_credentials(" secret-s2 ", "1234-ABCD")
    assert (creds.espn_s2, creds.swid) == ("secret-s2", "{1234-ABCD}")
    assert "secret-s2" not in repr(creds)
    assert "1234" not in repr(creds)
