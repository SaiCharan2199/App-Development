"""Unit tests for pipeline/rules.py: every filter rule, with tricky cases."""

import pytest

from pipeline import rules
from pipeline.transform import load_skills


# ---------------- Text helpers ----------------

def test_html_to_text_handles_escaped_html():
    html = "&lt;p&gt;&lt;b&gt;About us&lt;/b&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
    assert rules.html_to_text(html) == "About us\nPython"


def test_html_to_text_empty():
    assert rules.html_to_text(None) == ""


def test_split_sentences_keeps_us_abbreviation_together():
    sentences = rules.split_sentences("You must be a U.S. citizen. We offer benefits, e.g. dental.")
    assert sentences == ["You must be a US citizen.", "We offer benefits, eg dental."]


# ---------------- Title ----------------

@pytest.mark.parametrize("raw, expected", [
    ("Sr. Data Eng", "Senior Data Engineer"),
    ("Jr Software Engineer", "Junior Software Engineer"),
    ("SWE II", "Software Engineer II"),
    ("Data  Engineer ", "Data Engineer"),
    ("DevOps Engineer", "DevOps Engineer"),
])
def test_normalize_title(raw, expected):
    assert rules.normalize_title(raw) == expected


# ---------------- Employment type ----------------

@pytest.mark.parametrize("title, text, hint, expected", [
    ("Anything", "", "Full-time", "full_time"),
    ("Anything", "", "Intern", "internship"),
    ("Anything", "", "Contract", "contract"),
    ("Anything", "", "Part-time", "part_time"),
    ("Software Engineering Intern", "", None, "internship"),
    ("Data Engineer (Contract)", "", None, "contract"),
    ("Data Engineer", "This is a 6-month contract role.", None, "contract"),
    ("Data Engineer", "Open to C2C candidates.", None, "contract"),
    ("Data Engineer", "This is a full-time position.", None, "full_time"),
    ("Data Engineer", "Join our team.", None, "unknown"),
])
def test_detect_employment_type(title, text, hint, expected):
    assert rules.detect_employment_type(title, text, hint) == expected


# ---------------- Contract types ----------------

@pytest.mark.parametrize("text, expected", [
    ("This position is W2 only. No C2C.", ["w2"]),
    ("Open to W2 or C2C candidates.", ["c2c", "w2"]),
    ("Corp-to-Corp and 1099 contractors welcome.", ["1099", "c2c"]),
    ("We cannot work with C2C vendors. W2 contract only.", ["w2"]),
    ("We offer a 401k and health insurance.", []),
    ("This is a 1099 independent contractor engagement.", ["1099"]),
    # Found in real data: tax paperwork, not a contract job.
    ("Run reviews for vendors and missing tax forms; drive 1099 and TIN readiness for year-end.", []),
])
def test_detect_contract_types(text, expected):
    found, evidence = rules.detect_contract_types(text)
    assert found == expected
    assert (evidence is not None) == bool(expected)


# ---------------- Years and level ----------------

@pytest.mark.parametrize("text, expected", [
    ("You have 5+ years of experience with Python. 2+ years with Spark.", 5),
    ("3-5 years of professional experience", 3),
    ("At least 7 years of industry experience.", 7),
    ("We are a 10 year old company.", None),           # no 'experience' word
    ("Competitive salary.", None),
])
def test_detect_years_experience(text, expected):
    assert rules.detect_years_experience(text) == expected


@pytest.mark.parametrize("title, years, expected", [
    ("Senior Data Engineer", None, "senior"),
    ("Sr. Software Engineer", None, "senior"),
    ("Staff Software Engineer", None, "lead"),
    ("Principal ML Engineer", None, "lead"),
    ("Software Engineer II", None, "mid"),
    ("Software Engineer I", None, "entry"),
    ("New Grad Software Engineer", None, "entry"),
    ("Data Engineering Intern", None, "entry"),
    ("Engineering Manager, Data", None, "manager"),
    ("Senior Engineering Manager, Platform", None, "manager"),
    ("Manager, Customer Support Engineering", None, "manager"),
    ("Director of Data Science", None, "director_plus"),
    ("VP, Engineering", None, "director_plus"),
    ("Product Manager, Growth", None, "unknown"),        # not a people manager
    ("GTM Finance Manager", None, "unknown"),            # not a people manager
    ("Senior Partner Operations Manager", None, "senior"),
    ("Data Engineer", 1, "entry"),                        # falls back to years
    ("Data Engineer", 4, "mid"),
    ("Data Engineer", 8, "senior"),
    ("Data Engineer", None, "unknown"),
])
def test_detect_experience_level(title, years, expected):
    assert rules.detect_experience_level(title, years) == expected


# ---------------- Role family ----------------

@pytest.mark.parametrize("title, expected", [
    ("Senior Data Engineer", "data_engineer"),
    ("Analytics Engineer", "data_engineer"),
    ("Data Platform Engineer", "data_engineer"),
    ("Sr. Data Eng", "data_engineer"),
    ("Data Engineering Manager", "data_engineer"),
    ("Machine Learning Engineer", "ml_engineer"),
    ("Senior ML Engineer, Ranking", "ml_engineer"),
    ("Research Engineer, Interpretability", "ml_engineer"),
    ("Applied Scientist", "ml_engineer"),
    ("AI Engineer - Forward Deployed", "ml_engineer"),
    ("Data Scientist", "data_scientist"),
    ("Data Scientist, Machine Learning", "data_scientist"),
    ("Data Science Manager", "data_scientist"),
    ("Product Analyst", "data_analyst"),
    ("Business Intelligence Analyst", "data_analyst"),
    ("Software Engineer II", "software_engineer"),
    ("Senior Backend Engineer", "software_engineer"),
    ("Full Stack Developer", "software_engineer"),
    ("Site Reliability Engineer", "software_engineer"),
    ("Member of Technical Staff", "software_engineer"),
    ("Engineering Manager, Platform", "software_engineer"),
    ("SWE", "software_engineer"),
    ("Recruiter, Applied AI", "other"),                 # was wrongly AI/ML in the quick report
    ("Partner Account Manager, AI-Native Partners", "other"),
    ("Sales Engineer", "other"),
    ("Solutions Engineer", "other"),
    ("Account Executive", "other"),
])
def test_detect_role_family(title, expected):
    assert rules.detect_role_family(title) == expected


# ---------------- Sponsorship ----------------

@pytest.mark.parametrize("text, expected", [
    ("Candidates must be authorized to work in the U.S. without the need for visa sponsorship.", "no"),
    ("We are unable to sponsor visas for this role.", "no"),
    ("We do not provide visa sponsorship.", "no"),
    ("Sponsorship is not available for this position.", "no"),
    ("No visa sponsorship.", "no"),
    ("You must be a U.S. citizen.", "no"),
    ("US citizenship is required.", "no"),
    ("Visa sponsorship is available for this position.", "yes"),
    ("We will provide H-1B sponsorship.", "yes"),
    ("H-1B transfers are welcome.", "yes"),
    ("Great benefits and a fun team.", "unknown"),
    ("Will you now or in the future require sponsorship?", "unknown"),   # application question
    ("Visa sponsorship details will be provided during the interview.", "unknown"),
])
def test_detect_sponsorship(text, expected):
    value, evidence = rules.detect_sponsorship(text)
    assert value == expected
    assert (evidence is not None) == (expected != "unknown")


@pytest.mark.parametrize("text", [
    # Found in real data: about export licences, not visas (was wrongly 'no' for 472 jobs).
    "Any offer may be conditioned on your authorization to receive technology controlled under "
    "US export laws without sponsorship for an export license.",
    # Unfilled recruiter template left in a real posting.
    "[ONLY KEEP THIS IF WE DO NOT SPONSOR THE ROLE] Please note that visa sponsorship is not available.",
])
def test_sponsorship_ignores_non_visa_sentences(text):
    assert rules.detect_sponsorship(text)[0] == "unknown"


def test_sponsorship_no_wins_over_yes():
    text = "We sponsor visas for some teams. This role is not eligible for visa sponsorship."
    assert rules.detect_sponsorship(text)[0] == "no"


# ---------------- Clearance ----------------

@pytest.mark.parametrize("text, expected", [
    ("An active Secret clearance is required.", True),
    ("Must be able to obtain a security clearance.", True),
    ("TS/SCI clearance preferred.", True),
    ("No clearance needed.", None),
    ("Great benefits.", None),
])
def test_detect_clearance(text, expected):
    assert rules.detect_clearance(text) is expected


# ---------------- Salary ----------------

@pytest.mark.parametrize("text, expected", [
    ("The base salary range is $150,000 - $180,000 USD.", (150000, 180000, "USD", "year")),
    ("Pay: $120K–$150K", (120000, 150000, "USD", "year")),
    ("$150-180K per year", (150000, 180000, "USD", "year")),
    ("$55 - $65 per hour", (55, 65, "USD", "hour")),
    ("$40 to $50/hr", (40, 50, "USD", "hour")),
    ("CA$110,000 - CA$140,000", (110000, 140000, "CAD", "year")),
])
def test_parse_salary(text, expected):
    s = rules.parse_salary(text)
    assert (s["min"], s["max"], s["currency"], s["period"]) == expected


@pytest.mark.parametrize("text", [
    "Competitive pay",
    "Raised $200 - $300 million in funding",     # outside a plausible salary range
    "$180,000 - $150,000",                       # min greater than max
])
def test_parse_salary_rejects_non_salaries(text):
    assert rules.parse_salary(text) is None


# ---------------- Country and remote ----------------

@pytest.mark.parametrize("location, expected", [
    ("Austin, TX", "US"),
    ("San Francisco, CA", "US"),
    ("USA - Austin (dbt)", "US"),
    ("Remote - US", "US"),
    ("New York", "US"),
    ("London, UK; New York, NY", "US"),          # any US location counts
    ("Toronto, ON, CA", "CA"),                    # CA here is Canada, not California
    ("Vancouver, WA", "US"),
    ("Bengaluru, India", "IN"),
    ("Novi Sad, South Bačka, Serbia, EMEA", "RS"),
    ("Remote - AZ; Remote - FL; Remote - TX", "US"),      # states after a dash
    ("Remote - CA", "US"),
    ("Zürich, CH", "CH"),
    ("Milan, Italy", "IT"),
    ("Riyadh, Saudi Arabia", "SA"),
    ("San José, Costa Rica", "CR"),                       # not San Jose, California
    ("Rome, GA", "US"),                                    # US state wins over the Italian city
    ("Hybrid; Washington, DC", "US"),                      # city from the offices list
    ("Remote - Latin America", None),
    ("Remote", None),
    ("", None),
])
def test_detect_country(location, expected):
    assert rules.detect_country(location) == expected


@pytest.mark.parametrize("location, workplace, expected", [
    ("Remote - Worldwide", None, (True, True)),
    ("Remote - US", None, (True, False)),
    ("Seattle, WA", "remote", (True, False)),
    ("Seattle, WA", "hybrid", (False, False)),
])
def test_detect_remote(location, workplace, expected):
    assert rules.detect_remote(location, workplace) == expected


# ---------------- Skills ----------------

@pytest.fixture(scope="module")
def skill_patterns():
    return rules.compile_skill_patterns(load_skills())


def test_detect_skills_finds_common_skills(skill_patterns):
    text = "Use Python to build pipelines in Spark, SQL, and Go. Experience with C++, CI/CD and AWS."
    assert rules.detect_skills(text, skill_patterns) == [
        "AWS", "Apache Spark", "C++", "CI/CD", "Go", "Python", "SQL",
    ]


def test_detect_skills_ignores_lookalikes(skill_patterns):
    text = "Join our go-to-market team and go above and beyond. NoSQL stores. JavaScript only."
    found = rules.detect_skills(text, skill_patterns)
    assert "Go" not in found
    assert "SQL" not in found          # 'NoSQL' is not 'SQL'
    assert "Java" not in found         # 'JavaScript' is not 'Java'
    assert "JavaScript" in found
