from __future__ import annotations

import datetime as dt
import pathlib
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from rto import __version__
from rto.config import get_settings

TEMPLATES = Jinja2Templates(directory=str(pathlib.Path(__file__).parent / "templates"))


def pct(v: float | None, digits: int = 0) -> str:
    return "–" if v is None else f"{v * 100:.{digits}f}%"


def num(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{v:g}"


def month_label(d: dt.date | None) -> str:
    return d.strftime("%B %Y") if d else "–"


def week_label(d: dt.date) -> str:
    end = d + dt.timedelta(days=6)
    return f"{d:%b %-d}–{end:%-d}" if d.month == end.month else f"{d:%b %-d}–{end:%b %-d}"


TEMPLATES.env.filters.update(pct=pct, num=num, month_label=month_label, week_label=week_label)
TEMPLATES.env.globals.update(version=__version__)


def render(request: Request, name: str, status_code: int = 200, **ctx: Any):
    from rto.auth.deps import csrf_token

    ctx.setdefault("user", None)
    ctx["request"] = request
    ctx["csrf"] = csrf_token(request)
    ctx["auth_mode"] = get_settings().auth_mode
    ctx["environment"] = get_settings().environment
    return TEMPLATES.TemplateResponse(request, name, ctx, status_code=status_code)
