"""Plugin manifests and console CRUD. All offline, all in a temp dir."""

from __future__ import annotations

import pytest

from trade_monitor.plugin_admin import (
    PluginError,
    create,
    delete,
    update,
    user_dir,
    validate_name,
)
from trade_monitor.plugins import (
    all_manifests,
    load_disabled,
    scan_spec_manifests,
    set_enabled,
)


@pytest.fixture
def base(tmp_path):
    """An isolated per-user root so tests never touch ~/.trade-monitor."""
    return tmp_path


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------


def test_all_manifests_covers_all_five_kinds(base):
    mans = all_manifests(base=base)
    assert set(mans) == {"strategy", "feed", "pattern", "validator", "delivery"}


def test_builtin_strategy_manifests_present(base):
    from pathlib import Path

    from trade_monitor.strategy import REGISTRY as S
    from trade_monitor.strategy.loader import discover

    discover(S, [Path("strategies")])
    names = [m.name for m in all_manifests(base=base)["strategy"]]
    assert "canslim" in names


def test_scan_spec_manifests_reads_toml(base):
    d = base / "feeds"
    d.mkdir()
    (d / "myfeed.toml").write_text(
        '[feed]\nname="myfeed"\n[feed.http]\nurl="https://x/{symbol}"\n'
        '[feed.map]\nbars="$.b"\ndate="t"\nopen="o"\nhigh="h"\nlow="l"\nclose="c"\n'
    )
    mans = scan_spec_manifests("feed", base=base)
    assert [m.name for m in mans] == ["myfeed"]
    assert mans[0].origin == "spec"
    assert mans[0].status == "ok"


def test_broken_spec_is_listed_as_error(base):
    d = base / "feeds"
    d.mkdir()
    (d / "broken.toml").write_text("[feed]\nname=\"broken\"\n")  # no url
    mans = scan_spec_manifests("feed", base=base)
    assert mans[0].status == "error"
    assert mans[0].reason
    assert mans[0].name == "broken"


# --------------------------------------------------------------------------
# Enable / disable
# --------------------------------------------------------------------------


def test_set_enabled_persists(base):
    set_enabled("feed", "myfeed", False, base=base)
    assert ("feed", "myfeed") in load_disabled(base)
    set_enabled("feed", "myfeed", True, base=base)
    assert ("feed", "myfeed") not in load_disabled(base)


def test_disabled_spec_shows_status_disabled(base):
    d = base / "feeds"
    d.mkdir()
    (d / "f.toml").write_text(
        '[feed]\nname="f"\n[feed.http]\nurl="https://x/{symbol}"\n'
        '[feed.map]\nbars="$.b"\ndate="t"\nopen="o"\nhigh="h"\nlow="l"\nclose="c"\n'
    )
    set_enabled("feed", "f", False, base=base)
    mans = scan_spec_manifests("feed", base=base)
    assert mans[0].status == "disabled"
    assert mans[0].enabled is False


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


def test_create_feed_writes_valid_toml(base):
    path = create("feed", "my_rest", base=base, url="https://api/{symbol}")
    assert path.exists()
    assert path.parent == user_dir("feed", base)
    # The generated file must itself parse.
    from trade_monitor.datafeed.spec_feed import load_spec_file
    spec = load_spec_file(path)
    assert spec.name == "my_rest"
    assert spec.url == "https://api/{symbol}"


def test_create_strategy_writes_starter(base):
    path = create("strategy", "my_strategy", base=base)
    text = path.read_text()
    assert "class MyStrategy" in text
    assert "STRATEGY = MyStrategy()" in text


def test_create_rejects_duplicate(base):
    create("feed", "dup", base=base, url="https://x/{symbol}")
    with pytest.raises(PluginError):
        create("feed", "dup", base=base, url="https://x/{symbol}")


def test_create_feed_requires_url(base):
    with pytest.raises(PluginError):
        create("feed", "nourl", base=base, url="")


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "A-B", "_x", "x", ""])
def test_validate_name_rejects_unsafe(bad):
    with pytest.raises(PluginError):
        validate_name(bad)


def test_delete_removes_user_plugin(base):
    path = create("feed", "gone", base=base, url="https://x/{symbol}")
    delete("feed", "gone", base=base)
    assert not path.exists()


def test_delete_refuses_builtin(base):
    with pytest.raises(PluginError):
        delete("feed", "yfinance", base=base)


def test_update_description(base):
    create("feed", "desc", base=base, url="https://x/{symbol}", description="old")
    update("feed", "desc", base=base, description="new")
    from trade_monitor.datafeed.spec_feed import load_spec_file
    assert load_spec_file(user_dir("feed", base) / "desc.toml").description == "new"


def test_update_enabled_only_changes_state(base):
    create("feed", "tog", base=base, url="https://x/{symbol}")
    update("feed", "tog", base=base, enabled=False)
    assert ("feed", "tog") in load_disabled(base)
