"""Cheap English-centric screening heuristics, deliberately not an appraisal."""

from pathlib import Path
import re

VERSION = "heuristic-v1"
WORDS = frozenset(Path(__file__).with_name("words.txt").read_text().split())
WEIGHTS = {"brevity": 25, "spelling": 25, "sound": 20, "familiarity": 20, "restraint": 10}
PROFILES = {
    "general": {"label": "Balanced", "description": "An English-centric starting point for readable names."},
    "solid": {"label": "Solid & established", "description": "Familiar words, straightforward spelling, and grounded associations."},
    "catchy": {"label": "Catchy & playful", "description": "Short, rhythmic names with bright or lively associations."},
    "scientific": {"label": "Scientific & precise", "description": "Clean spelling and associations with discovery, light, space, or materials."},
}
ASSOCIATIONS = {
    "solid": set("anchor atlas bridge cedar crest field forge granite harbor haven iron oak ridge root slate solid steady steel stone timber trust".split()),
    "catchy": set("berry bloom bolt breeze cherry crisp echo ember finch fizz fox glow kite lemon lime mint otter pebble plum poppy spark swift wren".split()),
    "scientific": set("atlas axis cobalt comet delta echo element flux ion lattice light lucid lumen lunar nova orbit photon prism quantum solar spectrum terra vector".split()),
}
PROFILE_WEIGHTS = {
    "solid": {"brevity": 10, "spelling": 25, "sound": 10, "familiarity": 20, "restraint": 10, "association": 25},
    "catchy": {"brevity": 25, "spelling": 15, "sound": 25, "familiarity": 5, "restraint": 5, "association": 25},
    "scientific": {"brevity": 10, "spelling": 25, "sound": 10, "familiarity": 10, "restraint": 10, "association": 35},
}


def normalize(value: str) -> str:
    domain = value.strip().lower().removesuffix(".")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.com", domain):
        raise ValueError("Expected an ASCII second-level .com domain (no URL or subdomain)")
    if domain.startswith("xn--"):
        raise ValueError("Internationalized domains are outside the v1 scoring model")
    return domain


def words_for(label: str) -> list[str]:
    if label in WORDS:
        return [label]
    splits = [(label[:i], label[i:]) for i in range(3, len(label) - 2)
              if label[:i] in WORDS and label[i:] in WORDS]
    if splits:
        return list(min(splits, key=lambda pair: abs(len(pair[0]) - len(pair[1]))))
    return []


def score(domain: str, profile="general") -> dict:
    if profile not in PROFILES:
        raise ValueError("Unknown ranking profile")
    label = normalize(domain)[:-4]
    parts = words_for(label)
    clean = label.isalpha()
    vowel_groups = len(re.findall(r"[aeiouy]+", label))
    consonant_run = max((len(x) for x in re.findall(r"[bcdfghjklmnpqrstvwxz]+", label)), default=0)
    repeated = bool(re.search(r"(.)\1\1", label))
    # Real words contain legitimate consonant clusters; soften this penalty for known words.
    sound = max(0, 100 - max(0, consonant_run - (3 if parts else 2)) * 22
                - (40 if vowel_groups == 0 else 0) - max(0, vowel_groups - 4) * 12)
    wrapper = label.startswith(("get", "try", "use", "my")) and not (len(parts) == 1)
    values = {
        "brevity": max(0, 100 - max(0, len(label) - 5) * 9),
        "spelling": max(0, 100 - (55 if not clean else 0) - (25 if repeated else 0)),
        "sound": sound,
        "familiarity": 100 if len(parts) == 1 else 85 if len(parts) == 2 else 35,
        "restraint": 45 if wrapper else 100,
    }
    reasons = {
        "brevity": f"{len(label)} characters before .com",
        "spelling": "Letters only; no triple repeats" if clean and not repeated else "Digits, hyphens, or triple repeats add friction",
        "sound": f"{vowel_groups} vowel groups; longest consonant run {consonant_run} (rough proxy)",
        "familiarity": "Recognized: " + " + ".join(parts) if parts else "Outside the small starter word list; may still be a great name",
        "restraint": "Possible get/try/use/my wrapper" if wrapper else "No common wrapper detected",
    }
    weights = PROFILE_WEIGHTS.get(profile, WEIGHTS)
    if profile != "general":
        matches = sorted(set(parts) & ASSOCIATIONS[profile])
        values["association"] = 100 if matches else 25
        reasons["association"] = ("Curated tone words: " + ", ".join(matches)) if matches else "No curated tone match; company fit needs human judgment"
    features = {key: {"value": val, "weight": weights[key], "reason": reasons[key]} for key, val in values.items()}
    return {"score": round(sum(values[k] * weights[k] / 100 for k in values), 1),
            "version": VERSION, "profile": profile, "features": features, "words": parts}
