"""Format observations without caching effectful calls or inventing source code."""
import re

_CALLBACK = re.compile(r"^-- callback:")
# Recording a pure value constructor and then its use is redundant. Calls that
# create objects, play tweens, make requests, or invoke user functions stay put.
_PURE_VALUE = re.compile(
    r'^(?:game:GetService\("[A-Za-z_]\w*"\)|'
    r'(?:Vector2|Vector3|Color3|UDim|UDim2|TweenInfo|NumberSequence|NumberSequenceKeypoint|ColorSequence|ColorSequenceKeypoint)\.[A-Za-z_]\w*\(.*\))$'
)


def tidy(lines):
    """Keep callback scenarios distinct and retain every effectful observation.

    Repeated expression text does not establish object identity. In particular,
    TweenService:Create(...):Play() must never become a cached local or cause a
    preceding independent call to be discarded.
    """
    sections, current = [], []
    for line in lines:
        if _CALLBACK.match(line) and current:
            sections.append(current)
            current = []
        current.append(line)
    if current:
        sections.append(current)
    out = []
    for section in sections:
        for index, line in enumerate(section):
            if _PURE_VALUE.fullmatch(line) and any(
                line != later and line in later
                for later in section[index + 1:] if not later.startswith('--')
            ):
                continue
            out.append(line)
    return out
