"""Rule-based detection of job filters from titles and descriptions.

Every function is small and pure (text in, value out), so each one can be
unit-tested on its own. In Phase 3, Claude will do the same detection and we
will compare its accuracy against these rules.
"""

import re
from html import unescape

from bs4 import BeautifulSoup

# =============================================================
# Text helpers
# =============================================================


def html_to_text(html: str | None) -> str:
    """Convert (possibly HTML-escaped) HTML into clean plain text, one block per line."""
    if not html:
        return ""
    text = BeautifulSoup(unescape(html), "html.parser").get_text("\n")
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences (also on line breaks), without breaking on 'U.S.' or 'e.g.'."""
    text = re.sub(r"\bU\.S\.(A\.)?", "US", text or "")
    text = re.sub(r"\b(e\.g|i\.e|etc|vs|approx|incl)\.", lambda m: m.group(1).replace(".", ""), text, flags=re.I)
    sentences = []
    for line in text.splitlines():
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", line)
        sentences.extend(s.strip() for s in parts if s.strip())
    return sentences


# Sentences that use the word "sponsorship" but are not about visas,
# or are unfilled recruiter templates.
_NOT_VISA_SPONSORSHIP = re.compile(r"\bexport\b|\bITAR\b|\bonly keep this\b", re.I)


def _first_sentence_matching(sentences: list[str], patterns: list[re.Pattern]) -> str | None:
    for sentence in sentences:
        if sentence.endswith("?"):          # skip application questions
            continue
        if _NOT_VISA_SPONSORSHIP.search(sentence):
            continue
        if any(p.search(sentence) for p in patterns):
            return sentence
    return None


# =============================================================
# Title
# =============================================================

_TITLE_ABBREVIATIONS = [
    (r"\bSr\.?(?=\s|,|$)", "Senior"),
    (r"\bJr\.?(?=\s|,|$)", "Junior"),
    (r"\bEngr?\.?(?=\s|,|\)|$)", "Engineer"),
    (r"\bMgr\.?(?=\s|,|$)", "Manager"),
    (r"\bSWE\b", "Software Engineer"),
    (r"\bMLE\b", "Machine Learning Engineer"),
    (r"\bDev\b(?!\s*Ops)", "Developer"),
]


def normalize_title(title: str) -> str:
    """Expand common abbreviations: 'Sr. Data Eng' -> 'Senior Data Engineer'."""
    result = title or ""
    for pattern, replacement in _TITLE_ABBREVIATIONS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip()


# =============================================================
# Employment type
# =============================================================

_COMMITMENT_HINTS = [
    ("internship", r"intern|co-?op"),
    ("part_time", r"part"),
    ("temporary", r"temp|seasonal"),
    ("contract", r"contract|freelance|consult"),
    ("full_time", r"full|permanent|regular"),
]


def detect_employment_type(title: str, text: str, hint: str | None = None) -> str:
    """full_time / part_time / contract / internship / temporary / unknown.

    `hint` is a structured value from the source (e.g. Lever's 'commitment').
    """
    if hint:
        h = hint.lower()
        for value, pattern in _COMMITMENT_HINTS:
            if re.search(pattern, h):
                return value

    t = (title or "").lower()
    if re.search(r"\bintern(ship)?s?\b|\bco-?op\b", t):
        return "internship"
    if re.search(r"\bcontract(or)?\b|\bc2h\b|\bcontract[- ]to[- ]hire\b", t):
        return "contract"
    if re.search(r"\bpart[- ]time\b", t):
        return "part_time"
    if re.search(r"\btemporary\b|\btemp\b|\bseasonal\b", t):
        return "temporary"

    x = (text or "").lower()
    if re.search(
        r"\bthis is an? (\d+[- ]month )?contract\b|\bcontract (role|position|assignment|engagement)\b"
        r"|\bcontract[- ]to[- ]hire\b|\bc2c\b|\bcorp[- ]to[- ]corp\b|\b1099 (contract|basis|position)\b",
        x,
    ):
        return "contract"
    if re.search(r"\bthis is a part[- ]time\b|\bpart[- ]time (role|position|job)\b", x):
        return "part_time"
    if re.search(r"\bfull[- ]time\b", x):
        return "full_time"
    return "unknown"


# =============================================================
# Contract types (C2C / W2 / 1099)
# =============================================================

_CONTRACT_TERMS = {
    "c2c": re.compile(r"\b(c2c|corp[- ]?(to|2)[- ]?corp)\b", re.I),
    "w2": re.compile(r"\bw-?2\b", re.I),
    "1099": re.compile(r"\b1099\b", re.I),
}
_CONTRACT_CONTEXT = re.compile(
    r"contract|contractor|independent|basis|employment|employee|position|role|candidate|consultant|vendor|only",
    re.I,
)
_CONTRACTOR_WORDS = re.compile(r"\b(contractors?|independent|contract (role|position|basis|engagement)|1099 (basis|contract))\b", re.I)
_NEGATION_BEFORE = re.compile(r"\b(no|not|non|never|cannot|can't|unable|excluding|except|without)\b[\s\w/,-]{0,15}$", re.I)


def detect_contract_types(text: str) -> tuple[list[str], str | None]:
    """Return (sorted contract types allowed, evidence sentence). 'No C2C' is NOT counted."""
    found: set[str] = set()
    evidence = None
    for sentence in split_sentences(text):
        hits = {name: p.search(sentence) for name, p in _CONTRACT_TERMS.items()}
        hits = {name: m for name, m in hits.items() if m}
        if not hits:
            continue
        # Need a contract-related word nearby (or two terms together, e.g. "W2 or C2C").
        if not _CONTRACT_CONTEXT.search(sentence) and len(hits) < 2:
            continue
        for name, match in hits.items():
            before = sentence[: match.start()]
            if _NEGATION_BEFORE.search(before):
                continue
            # '1099' also appears in tax paperwork ("1099 forms"); only count it for contractors.
            if name == "1099" and not _CONTRACTOR_WORDS.search(sentence):
                continue
            found.add(name)
        evidence = evidence or sentence
    return sorted(found), (evidence if found else None)


# =============================================================
# Experience level and years
# =============================================================

_NOT_PEOPLE_MANAGER = re.compile(
    r"\b(product|program|project|account|partner|customer success|success|marketing|sales|channel|territory"
    r"|community|solutions?|case|office|facilities|vendor|relationship|portfolio|technical program|brand"
    r"|content|event|implementation|onboarding|operations program)\s+manager\b",
    re.I,
)
# Titles that clearly mean managing a team ("Engineering Manager", "Manager, Data Science").
# A bare "... Manager" (e.g. "Finance Manager") is usually an individual role, so it is NOT counted.
_PEOPLE_MANAGER = re.compile(
    r"\b(engineering|software|data|analytics|data science|science|machine learning|ml|ai|research|security"
    r"|infrastructure|platform|design|product design|qa|quality|it|technical|development|devops|sre)\s+manager\b"
    r"|\bmanager\s*,|\bmanager of\b|\bmanager\s*[-–]\s|\bmanager,?\s+(engineering|software|data|security)\b",
    re.I,
)
_LEVEL_RULES = [
    ("director_plus", re.compile(r"\b(director|head of|vice president|vp|svp|evp|chief|cto|cio|cdo|cfo|ceo)\b", re.I)),
    ("lead", re.compile(r"\b(staff|principal|distinguished|lead|tech lead)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr|iii|iv|level 3)\b", re.I)),
    ("entry", re.compile(
        r"\b(junior|jr|entry[- ]level|entry|new grad(uate)?|graduate|early[- ]career|apprentice|intern(ship)?|university grad)\b"
        r"|\b(engineer|developer|analyst|scientist|associate)\s+i\b|^associate\b", re.I)),
    ("mid", re.compile(r"\b(ii|mid[- ]level|intermediate)\b", re.I)),
]

_YEARS = re.compile(
    r"(?<![\d$])(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?)?\s*(?:years?|yrs?)\b",
    re.I,
)
_EXPERIENCE_WORDS = re.compile(r"experience|background|industry|professional|working", re.I)


def detect_years_experience(text: str) -> int | None:
    """First 'N+ years of experience' requirement in the text, e.g. '5+ years' -> 5."""
    for sentence in split_sentences(text):
        if not _EXPERIENCE_WORDS.search(sentence):
            continue
        match = _YEARS.search(sentence)
        if match:
            years = int(match.group(1))
            if 0 <= years <= 30:
                return years
    return None


def detect_experience_level(title: str, years: int | None = None) -> str:
    """entry / mid / senior / lead / manager / director_plus / unknown."""
    t = title or ""
    if _LEVEL_RULES[0][1].search(t):
        return "director_plus"
    if _PEOPLE_MANAGER.search(t) and not _NOT_PEOPLE_MANAGER.search(t):
        return "manager"
    for level, pattern in _LEVEL_RULES[1:]:
        if pattern.search(t):
            return level
    if years is not None:
        if years <= 2:
            return "entry"
        if years <= 5:
            return "mid"
        return "senior"
    return "unknown"


# =============================================================
# Role family (also applied to H-1B job titles, so the two can be matched)
# =============================================================

ROLE_FAMILIES = ("data_engineer", "ml_engineer", "data_scientist", "data_analyst", "software_engineer", "other")

_ROLE_RULES = [   # order matters: first match wins
    ("data_engineer", re.compile(
        r"\b(data|analytics|etl|big data|data platform|data infrastructure|data warehouse|data pipeline)\s+"
        r"(engineer|engineering|developer|architect)\b", re.I)),
    ("ml_engineer", re.compile(
        r"\b(machine learning|ml|ai|deep learning|llm|nlp|computer vision|genai|generative ai|applied ai|mlops)\b"
        r"[\w\s,/&()-]{0,40}?\b(engineer|engineering|developer|architect|scientist|researcher)\b"
        r"|\bresearch (engineer|scientist)\b|\bapplied scientist\b|\bmle\b", re.I)),
    ("data_scientist", re.compile(
        r"\bdata scien(ce|tist)\b|\bdecision scien(ce|tist)\b|\bquantitative (researcher|analyst)\b", re.I)),
    ("data_analyst", re.compile(
        r"\b(data|bi|business intelligence|analytics|product|business|reporting|insights|marketing)\s+analyst\b", re.I)),
    ("software_engineer", re.compile(
        r"\b(software|backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|mobile|ios|android|web|platform"
        r"|infrastructure|systems|site reliability|reliability|devops|cloud|security|firmware|embedded|application"
        r"|api|distributed systems|qa|test|automation|build|release|production|database|network)\s+"
        r"(engineer|developer|engineering)\b"
        r"|\bsre\b|\bswe\b|\bsoftware development engineer\b|\bprogrammer\b|\bmember of (the )?technical staff\b"
        r"|\bengineering manager\b|^(senior |sr\.? |staff |principal |lead )?(engineer|developer)\b", re.I)),
]


def detect_role_family(title: str) -> str:
    """Group a job title: data_engineer / ml_engineer / data_scientist / data_analyst / software_engineer / other."""
    t = normalize_title(title or "")
    for family, pattern in _ROLE_RULES:
        if pattern.search(t):
            return family
    return "other"


# =============================================================
# Visa sponsorship
# =============================================================

_SPONSOR_NO = [re.compile(p, re.I) for p in [
    r"\b(not|unable to|cannot|can ?not|can't|won't|will not|do not|does not|don't|is not able to|are not able to|no longer)\b[^.;]{0,40}\bsponsor",
    r"\bwithout (the )?(need for |requiring |requirement of |the need of )?(current or future |present or future |now or in the future |future )?(visa |employer |immigration |company |employment )?sponsorship\b",
    r"\bsponsorship\b[^.;]{0,30}\b(is |will |are )?(not|n't) (be )?(available|offered|provided|supported|possible)\b",
    r"\bno (visa |immigration |h-?1b |employment )?sponsorship\b",
    r"\bnot (eligible|able) (for|to (offer|provide)) (visa |immigration )?sponsorship\b",
    r"\bmust be (a )?(u\.?s\.? )?citizens?\b",
    r"\b(u\.?s\.? )?citizenship (is )?required\b",
]]
_SPONSOR_YES = [re.compile(p, re.I) for p in [
    r"\b(will|can|able to|happy to|we|do|does|may)\s+(provide|offer|support|sponsor)\b[^.;]{0,25}\b(visa|h-?1b|immigration|sponsorship)",
    r"\b(visa|h-?1b|immigration|employment) sponsorship (is |may be |will be )?(available|offered|provided|supported|possible)\b",
    r"\bsponsorship (is |may be )?available\b",
    r"\bh-?1b (transfers?|sponsorship) (are |is )?(welcome|accepted|supported|available)\b",
    r"\bopen to sponsor",
]]


def detect_sponsorship(text: str) -> tuple[str, str | None]:
    """Return ('yes' | 'no' | 'unknown', evidence sentence).

    'No' wins over 'yes' if both appear; nothing found stays 'unknown'.
    """
    sentences = split_sentences(text)
    evidence = _first_sentence_matching(sentences, _SPONSOR_NO)
    if evidence:
        return "no", evidence
    evidence = _first_sentence_matching(sentences, _SPONSOR_YES)
    if evidence:
        return "yes", evidence
    return "unknown", None


# =============================================================
# Security clearance
# =============================================================

_CLEARANCE = re.compile(
    r"\b(active|current|existing|ts/sci|top secret|secret|public trust|dod|government)\s+(security\s+)?clearance\b"
    r"|\bclearance (is )?required\b"
    r"|\b(obtain|maintain|eligible for|ability to obtain|able to obtain)\s+(a |an )?(u\.?s\.? )?(government |security )?clearance\b",
    re.I,
)


def detect_clearance(text: str) -> bool | None:
    """True if a security clearance is required; None if not mentioned."""
    for sentence in split_sentences(text):
        match = _CLEARANCE.search(sentence)
        if match and not re.search(r"\bno\s+(security\s+)?clearance", sentence, re.I):
            return True
    return None


# =============================================================
# Salary
# =============================================================

_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_SALARY_RANGE = re.compile(
    rf"(CA\$|C\$|US\$|\$)\s?{_NUMBER}\s*([kK])?\s*(?:USD|CAD)?\s*(?:-|–|—|to)\s*(?:CA\$|C\$|US\$|\$)?\s?{_NUMBER}\s*([kK])?"
)


def _to_number(raw: str, k: str | None) -> float:
    value = float(raw.replace(",", ""))
    return value * 1000 if k else value


def parse_salary(text: str) -> dict | None:
    """Find the first pay range: {'min', 'max', 'currency', 'period'} or None."""
    for match in _SALARY_RANGE.finditer(text or ""):
        symbol, low_raw, low_k, high_raw, high_k = match.groups()
        low = _to_number(low_raw, low_k or high_k)
        high = _to_number(high_raw, high_k)
        if low > high:
            continue
        after = text[match.end(): match.end() + 40].lower()
        around = text[max(0, match.start() - 20): match.end() + 10]
        if re.match(r"\s*(million|billion|mm|bn|m|b)\b", after):
            continue                                   # funding amounts, not pay
        if re.search(r"per hour|/\s?h(ou)?r|hourly|an hour", after):
            period = "hour"
        elif re.search(r"per month|/\s?mo(nth)?|monthly", after):
            period = "month"
        elif high >= 1000:
            period = "year"
        else:
            continue                                   # small numbers without 'per hour' are not pay
        plausible = {"year": (15_000, 2_000_000), "month": (1_000, 200_000), "hour": (8, 600)}[period]
        if not (plausible[0] <= low and high <= plausible[1]):
            continue
        currency = "CAD" if symbol in ("CA$", "C$") or "CAD" in around else "USD"
        return {"min": low, "max": high, "currency": currency, "period": period}
    return None


# =============================================================
# Location -> country, remote
# =============================================================

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
    "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
_US_STATE_NAMES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho"
    "|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota"
    "|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina"
    "|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas"
    "|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming"
)
_US_CITIES = (
    "nyc|san francisco|sf bay area|bay area|silicon valley|seattle|austin|boston|chicago|denver|los angeles"
    "|atlanta|miami|palo alto|mountain view|menlo park|sunnyvale|san jose|santa clara|san mateo|redwood city"
    "|foster city|oakland|portland|salt lake city|dallas|houston|phoenix|philadelphia|pittsburgh|raleigh"
    "|minneapolis|detroit|san diego|irvine|nashville|boulder|jersey city|brooklyn|cambridge, ma|washington,? d\\.?c"
)
_US_PATTERNS = [
    re.compile(r"\b(US|USA|U\.S\.A?\.?)\b"),
    re.compile(r"\bunited states\b|\bnorth america\b", re.I),
    re.compile(rf"\b({_US_STATE_NAMES})\b", re.I),
    re.compile(rf"\b({_US_CITIES})\b", re.I),
]
_STATE_ABBR = re.compile(r"(?:,|\s[-–]|^[-–])\s*([A-Z]{2})\b")   # 'Austin, TX' or 'Remote - TX'

_OTHER_COUNTRIES = {
    "IN": r"india|bengaluru|bangalore|hyderabad|pune|chennai|mumbai|gurgaon|gurugram|noida|new delhi|delhi",
    "GB": r"united kingdom|\buk\b|england|scotland|wales|london|edinburgh|manchester|cambridge, uk",
    "CA": r"canada|toronto|vancouver, bc|vancouver, british columbia|montreal|ontario|quebec|british columbia|ottawa|calgary|waterloo",
    "IE": r"ireland|dublin",
    "DE": r"germany|berlin|munich|hamburg|frankfurt",
    "FR": r"france|paris",
    "NL": r"netherlands|amsterdam",
    "ES": r"spain|madrid|barcelona",
    "PT": r"portugal|lisbon",
    "PL": r"poland|warsaw|krakow",
    "SE": r"sweden|stockholm",
    "DK": r"denmark|copenhagen",
    "CH": r"switzerland|z[uü]rich|geneva",
    "IT": r"italy|milan|rome",
    "GR": r"greece|athens",
    "NO": r"norway|oslo",
    "BE": r"belgium|brussels",
    "AT": r"austria|vienna",
    "FI": r"finland|helsinki",
    "IS": r"iceland|reykjav[ií]k",
    "LT": r"lithuania|vilnius",
    "EE": r"estonia|tallinn",
    "SA": r"saudi arabia|riyadh|jeddah",
    "QA": r"qatar|doha",
    "KW": r"kuwait",
    "TR": r"turkey|t[uü]rkiye|istanbul",
    "EG": r"egypt|cairo",
    "ZA": r"south africa|johannesburg|cape town",
    "NG": r"nigeria|lagos",
    "KE": r"kenya|nairobi",
    "CR": r"costa rica",
    "CL": r"chile|santiago",
    "PE": r"peru|lima",
    "ID": r"indonesia|jakarta",
    "MY": r"malaysia|kuala lumpur",
    "TH": r"thailand|bangkok",
    "VN": r"vietnam|viet nam|ho chi minh|hanoi",
    "TW": r"taiwan|taipei",
    "CZ": r"czech|prague",
    "RO": r"romania|bucharest",
    "RS": r"serbia|belgrade|novi sad",
    "IL": r"israel|tel aviv",
    "AE": r"united arab emirates|\buae\b|dubai|abu dhabi",
    "SG": r"singapore",
    "JP": r"japan|tokyo",
    "KR": r"korea|seoul",
    "CN": r"china|beijing|shanghai|shenzhen",
    "HK": r"hong kong",
    "AU": r"australia|sydney|melbourne",
    "NZ": r"new zealand|auckland",
    "BR": r"brazil|s[aã]o paulo",
    "MX": r"mexico|ciudad de m[eé]xico",
    "AR": r"argentina|buenos aires",
    "CO": r"colombia|bogot[aá]",
    "PH": r"philippines|manila",
}
_OTHER_PATTERNS = {code: re.compile(rf"\b({p})\b", re.I) for code, p in _OTHER_COUNTRIES.items()}
_WORLDWIDE = re.compile(r"\b(worldwide|anywhere|global(ly)?|international)\b", re.I)


def detect_country(location: str) -> str | None:
    """ISO country code from a location string. A job listing any US location counts as US."""
    if not location:
        return None
    states = {s for s in _STATE_ABBR.findall(location) if s in US_STATES}
    if states - {"CA"}:                       # e.g. ', TX' - clearly a US state
        return "US"
    us_names, us_cities = _US_PATTERNS[:3], _US_PATTERNS[3]
    if any(p.search(location) for p in us_names):      # 'USA', 'United States', 'Texas'
        return "US"
    for code, pattern in _OTHER_PATTERNS.items():      # checked before US city names, so
        if pattern.search(location):                   # 'San José, Costa Rica' is not San Jose, CA
            return code
    if us_cities.search(location) or states:           # 'Seattle', or only ', CA' = California
        return "US"
    return None


def detect_remote(location: str, workplace_type: str | None = None) -> tuple[bool, bool]:
    """Return (is_remote, remote_worldwide)."""
    loc = location or ""
    is_remote = (workplace_type or "").lower() == "remote" or bool(re.search(r"\bremote\b", loc, re.I))
    worldwide = is_remote and bool(_WORLDWIDE.search(loc))
    return is_remote, worldwide


# =============================================================
# Skills
# =============================================================


def compile_skill_patterns(skills: list[dict]) -> list[tuple[str, re.Pattern]]:
    """Build one regex per skill from its aliases (whole-word matches)."""
    compiled = []
    for skill in skills:
        aliases = sorted(skill.get("aliases") or [skill["name"]], key=len, reverse=True)
        body = "|".join(re.escape(a) for a in aliases)
        # Whole word: not preceded/followed by a letter or digit.
        regex = rf"(?<![\w+#.])(?:{body})(?![\w+#])"
        flags = re.IGNORECASE
        if skill.get("exact_case"):
            # Short names that are also English words ('Go', 'React'): exact case only,
            # and skip phrases like 'Go-to-market' or 'go above and beyond'.
            regex += r"(?!-)(?!\s(?:to|above|beyond|further|live|deep|ahead)\b)"
            flags = 0
        pattern = re.compile(regex, flags)
        compiled.append((skill["name"], pattern))
    return compiled


def detect_skills(text: str, skill_patterns: list[tuple[str, re.Pattern]]) -> list[str]:
    """Names of all skills mentioned in the text."""
    return sorted(name for name, pattern in skill_patterns if pattern.search(text or ""))
