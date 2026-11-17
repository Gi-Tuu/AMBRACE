"""Finite, literal plan/condition grammar. Unknown clauses never imply completion."""
import re

UNSAFE = re.compile(r"[“”「」『』\"'？?]|如果|假如|要是|倘若|假设|示例|写着|听说|据说|转述|发来|发回|说道|消息[：:]|记录[：:]|说的|提到|声称|转发|也许|或许|不确定|的话")
NEGATIVE = re.compile(r"没|未|不|别|可能|应该|将要|预计")
SPLIT = re.compile(r"[，,。；;！!\n]")
_GENERIC = re.compile(r"^(?:用户|我)(?:计划|打算|准备|需要|要)(?:完成|做完)?(?P<target>.+)$")
_CONTINUE = re.compile(r"^(?:还需要|另外需要|还要)(?:完成|做完)?(?P<target>.+)$")
_CONDITION = re.compile(r"^(?:用户|我)(?:今天|明天|今晚|稍后)?(?P<event>到达.+?|到家|到地方|到门口|在门口|吃药|吃完药|服药)(?:之后|以后|后)(?P<target>.+)$")
_ARRIVAL = re.compile(r"^(?:我)?(?:已经)?(?:到了|到达了?)(?P<target>.+?)(?:了)?$")
_HOME = re.compile(r"^(?:我)?(?:已经)?(?:到家|回家|回到家)(?:了|啦)?$")
_DOOR = re.compile(r"^(?:我)?(?:已经)?(?:到|在)(?P<target>门口|楼下)(?:了|啦)?$")
_RETURN = re.compile(r"^我(?:已经)?(?:进门|回来)(?:了|啦)$")
_MED = re.compile(r"^(?:我)?(?:已经)?(?:吃过药|吃药|药吃|吃了药|药已经吃)(?:了)?$")


def clauses(content):
    return [part.strip() for part in SPLIT.split(content or "") if part.strip()]


def _condition(event):
    if event in ("吃药","吃完药","服药"):
        return {"kind":"medication","target":"药"}
    if event.startswith("到达"):
        return {"kind":"arrival","target":event[2:]}
    return {"kind":"arrival","target":{"到家":"家","到门口":"门口","在门口":"门口"}.get(event,"")}


def source_plan_specs(content):
    if UNSAFE.search(content or "") or NEGATIVE.search(content or ""):
        return []
    parts = clauses(content)
    explicit_actor = bool(parts and re.match(r"^(?:用户|我)",parts[0]))
    result = []
    for ordinal, clause in enumerate(SPLIT.split(content or "")):
        clause = clause.strip()
        if not clause:
            continue
        conditional = _CONDITION.fullmatch(clause)
        generic = _GENERIC.fullmatch(clause) or (_CONTINUE.fullmatch(clause) if explicit_actor else None)
        match = conditional or generic
        if match is None:
            continue
        target = match.group("target").strip()
        if len(target) < 4:
            continue
        result.append({"ordinal":ordinal,"target":target,"clause":clause,
            "condition":_condition(conditional.group("event")) if conditional else None})
    return result


def condition_signals(content):
    if UNSAFE.search(content or "") or NEGATIVE.search(content or ""):
        return []
    result = []
    for clause in clauses(content):
        if _HOME.fullmatch(clause):
            result.append(("arrival","家"))
        elif (match := _DOOR.fullmatch(clause)):
            result.append(("arrival",match.group("target")))
        elif _RETURN.fullmatch(clause):
            # Existing first-person doorstep contract; never infer a named destination.
            result.append(("arrival","门口"))
        elif (match := _ARRIVAL.fullmatch(clause)):
            result.append(("arrival",match.group("target")))
        elif _MED.fullmatch(clause):
            result.append(("medication","药"))
    return result
