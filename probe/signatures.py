"""ATS detection signature tables — the declarative heart of probe.

Adding support for a new ATS is mostly a matter of adding rows here (plus an
adapter in jobwatch and, for GH/Lever/Ashby-style platforms, a confirmer in
`confirm.py`). Keeping these as data — not code — is what makes detection
open-for-extension: a new platform is a new row, not a new branch.
"""

# Types we have a working adapter for (must stay in sync with jobwatch.ADAPTERS).
SUPPORTED_TYPES = {
    "workday", "greenhouse", "lever", "ashby", "phenom", "successfactors",
    "oracle", "radancy", "smartrecruiters", "bamboohr", "rippling", "ripplematch",
    "ukg", "dayforce", "icims", "eightfold", "jazzhr", "jobvite", "avature",
}

# (type, host-regex). Matched against the final (post-redirect) URL's host and
# the original host. A host match is high confidence — it *is* the ATS.
HOST_RULES = [
    ("workday", r"\.myworkdayjobs\.com$|\.myworkdaysite\.com$"),
    ("greenhouse", r"(^|\.)(job-boards|boards)\.greenhouse\.io$"),
    ("lever", r"(^|\.)jobs\.lever\.co$"),
    ("ashby", r"(^|\.)jobs\.ashbyhq\.com$"),
    ("icims", r"\.icims\.com$"),
    ("smartrecruiters", r"(^|\.)careers\.smartrecruiters\.com$|(^|\.)jobs\.smartrecruiters\.com$"),
    ("bamboohr", r"\.bamboohr\.com$"),
    ("rippling", r"(^|\.)ats\.rippling\.com$"),
    ("ripplematch", r"(^|\.)app\.ripplematch\.com$"),
    ("ukg", r"\.ultipro\.(com|ca)$|recruiting\.ultipro"),
    ("dayforce", r"(^|\.)jobs\.dayforcehcm\.com$"),
    ("oracle", r"\.oraclecloud\.com$"),
    ("jazzhr", r"\.applytojob\.com$"),
    ("jobvite", r"(^|\.)jobs\.jobvite\.com$"),
    ("radancy", r"\.talentbrew\.com$|\.tbcdn\.talentbrew"),
    ("successfactors", r"\.sapsf\.com$|\.successfactors\.com$"),
]

# (type, html-regex, slug-capture-regex-or-None). Looked for in the page HTML to
# catch a white-labeled backend. The 2nd regex, if set, pulls the real slug from
# the first matching URL in the page. Medium confidence (HTML can lie — e.g.
# "Greenhouse Gas" ESG copy, a "Workday" job *title*) so GH/Lever/Ashby get
# actively confirmed before we trust them.
#
# ORDER MATTERS: most-specific signatures first. Front-end platforms (Radancy,
# Phenom) often embed links to a secondary ATS (a stray myworkdayjobs.com URL),
# so their own specific CDN/script signatures must be checked BEFORE Workday's
# generic host substring — otherwise a TalentBrew site gets mislabeled "workday".
HTML_SIGNATURES = [
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io|grnh\.se|greenhouse\.io/embed",
     r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co|api\.lever\.co",
     r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9_-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com|ashbyhq\.com/posting-api",
     r"jobs\.ashbyhq\.com/([a-z0-9_-]+)"),
    ("radancy", r"talentbrew|tbcdn|/search-jobs\?orgIds", None),
    ("phenom", r"var\s+phApp|phApp\.ddo|widgetApiEndpoint", None),
    ("eightfold", r"eightfold\.ai|pcsxConfig|/api/pcsx/", None),
    ("icims", r"\.icims\.com|iCIMS_JobsTable|iCIMS_JobCardItem", None),
    ("oracle", r"\.oraclecloud\.com|/hcmUI/CandidateExperience", None),
    ("dayforce", r"dayforcehcm\.com", None),
    ("smartrecruiters", r"smartrecruiters\.com", None),
    ("bamboohr", r"\.bamboohr\.com", None),
    ("rippling", r"ats\.rippling\.com", None),
    ("avature", r"avacdn\.net|avature\.portal\.id|avature\.wizard\.registrars", None),
    ("jazzhr", r"applytojob\.com|resumator_even_row|resumator_odd_row", None),
    ("jobvite", r"jobs\.jobvite\.com|jv-job-list|Powered by Jobvite|jv\.careersite", None),
    ("ukg", r"\.ultipro\.(?:com|ca)|recruiting\.ultipro", None),
    ("successfactors", r"successfactors\.com|rmkcdn|/sfcareer/", None),
    ("workday", r"\.myworkdayjobs\.com|\.myworkdaysite\.com", None),
]

# Recognised but unsupported — reported by name only (no adapter).
OTHER_ATS = [
    ("IBM/Infinite Brassring (Kenexa)", r"brassring\.com|kenexa"),
    ("Cornerstone OnDemand", r"\.csod\.com|cornerstoneondemand"),
    ("Workable", r"workable\.com|apply\.workable"),
    ("Yello", r"yello\.co"),
    ("Beamery", r"beamery\.com|beamery"),
    ("Recruitee", r"\.recruitee\.com"),
    ("Breezy HR", r"breezy\.hr"),
    ("Teamtailor", r"teamtailor\.com"),
    ("Paylocity", r"recruiting\.paylocity\.com"),
    ("ADP Workforce Now", r"workforcenow\.adp\.com|recruiting\.adp\.com"),
    ("Oracle Taleo", r"taleo\.net|tbe\.taleo"),
    ("Jobylon", r"jobylon\.com"),
    ("Personio", r"\.jobs\.personio\."),
    ("Pinpoint", r"pinpointhq\.com"),
    ("Workday (peakon/other)", r"\.wd\d+\."),
]

# For GH/Lever/Ashby a white-labeled board keeps the user-facing URL but pins the
# real slug in this per-type config field.
OVERRIDE_FIELD = {"greenhouse": "token", "lever": "company", "ashby": "board"}
