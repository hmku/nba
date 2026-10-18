"""Stable player identifiers.

`player_id` everywhere in the system is a lowercase ascii slug of the player's display name,
e.g. "Nikola Jokić" -> "nikola-jokic", "Jaren Jackson Jr." -> "jaren-jackson-jr".
"""

from __future__ import annotations

import re
import unicodedata

# Characters that NFKD does not decompose into ascii.
_TRANSLIT = str.maketrans(
    {
        "đ": "d",
        "Đ": "D",
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ı": "i",
        "ё": "e",  # Cyrillic (bbref spells Egor Dёmin with U+0451)
        "е": "e",
    }
)


def slugify(name: str) -> str:
    """Lowercase ascii slug: strip accents/punctuation, spaces and hyphens -> single hyphen."""
    s = (name or "").translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = s.replace("'", "").replace("’", "").replace(".", "")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# Known spelling variants across sources -> canonical slug (canonical = Basketball-Reference name).
ALIASES: dict[str, str] = {
    "nicolas-claxton": "nic-claxton",
    "cam-johnson": "cameron-johnson",
    "herb-jones": "herbert-jones",
    "kj-martin": "kenyon-martin-jr",
    "gg-jackson": "gg-jackson-ii",
    "alex-sarr": "alexandre-sarr",
    "bub-carrington": "carlton-carrington",
    "ron-holland": "ronald-holland-ii",
    "ronald-holland": "ronald-holland-ii",
    "derrick-jones": "derrick-jones-jr",
    "jabari-smith": "jabari-smith-jr",
    "jaren-jackson": "jaren-jackson-jr",
    "michael-porter": "michael-porter-jr",
    "kelly-oubre": "kelly-oubre-jr",
    "wendell-carter": "wendell-carter-jr",
    "gary-trent": "gary-trent-jr",
    "tim-hardaway": "tim-hardaway-jr",
    "larry-nance": "larry-nance-jr",
    "jaime-jaquez": "jaime-jaquez-jr",
    "trey-murphy": "trey-murphy-iii",
    "kevin-porter": "kevin-porter-jr",
    "dereck-lively": "dereck-lively-ii",
    "jimmy-butler-iii": "jimmy-butler",
    "moe-wagner": "moritz-wagner",
    "pj-washington-jr": "pj-washington",
    "bobby-portis-jr": "bobby-portis",
    "robert-williams-iii": "robert-williams",
    "walter-clayton-jr": "walter-clayton",
}


def canonical_id(name: str) -> str:
    s = slugify(name)
    return ALIASES.get(s, s)
