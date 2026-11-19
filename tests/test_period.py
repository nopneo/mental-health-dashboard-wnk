from datetime import date

import pytest

import app as appmod
import queries as q

TODAY = date(2026, 11, 19)      # ปีงบ 2570 เริ่มแล้ว (1 ต.ค. 2569)


def test_rolling_window_is_twelve_months_to_today():
    w = q.period(None, TODAY)
    assert (w.start, w.asof, w.fy) == (date(2025, 11, 19), TODAY, None)


def test_completed_fiscal_year_is_oct_to_sep():
    w = q.period(2569, TODAY)
    assert (w.start, w.asof, w.fy) == (date(2025, 10, 1), date(2026, 9, 30), 2569)


def test_current_fiscal_year_runs_to_today():
    w = q.period(2570, TODAY)
    assert (w.start, w.asof) == (date(2026, 10, 1), TODAY)


def test_months_ago_clamps_to_month_end():
    assert q._months_ago(date(2024, 2, 29), 12) == date(2023, 2, 28)
    assert q._months_ago(date(2026, 5, 31), 3) == date(2026, 2, 28)


def test_fy_choices_are_current_and_five_before():
    assert q.fy_choices(TODAY) == [2570, 2569, 2568, 2567, 2566, 2565]
    assert q.fy_choices(date(2026, 9, 30))[0] == 2569       # ก่อน 1 ต.ค. ยังเป็นปีงบ 2569


def test_registry_sql_uses_window_dates_not_curdate():
    sql = q._reg(q.period(2569, TODAY))
    assert "'2025-10-01'" in sql and "'2026-09-30'" in sql and "curdate" not in sql


class _Stop(Exception):
    pass


@pytest.fixture
def seen_fy(monkeypatch):
    got = []

    def fake_registry(force=False, fy=None):
        got.append(fy)
        raise _Stop

    monkeypatch.setattr(q, "registry", fake_registry)
    monkeypatch.setattr(appmod.queries, "fy_choices", lambda today=None: [2570, 2569, 2568])
    appmod.app.config["TESTING"] = True
    return got


@pytest.mark.parametrize("arg,want", [("?fy=2569", 2569), ("?fy=1999", None), ("?fy=abc", None), ("", None)])
def test_public_page_only_accepts_offered_fiscal_years(seen_fy, arg, want):
    with pytest.raises(_Stop):
        appmod.app.test_client().get("/" + arg)
    assert seen_fy == [want]
