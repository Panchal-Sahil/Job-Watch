"""ATS detection signature tables — adding a new ATS is mostly adding rows here."""

# Must stay in sync with jobwatch.ADAPTERS (a test enforces this).
SUPPORTED_TYPES = {
    "workday", "greenhouse", "lever", "ashby", "phenom", "successfactors",
    "oracle", "radancy", "smartrecruiters", "bamboohr", "rippling", "ripplematch",
    "ukg", "dayforce", "icims", "eightfold", "jazzhr", "jobvite", "avature", "gem",
    "workable", "yello", "zohorecruit",
}

# (type, host-regex). High confidence — the URL *is* the ATS.
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
    ("gem", r"(^|\.)jobs\.gem\.com$"),
    ("workable", r"(^|\.)apply\.workable\.com$"),
    ("yello", r"(^|\.)yello\.co$"),
    ("zohorecruit", r"\.zohorecruit\.(com|ca|eu|in|com\.au|jp)$"),
]

# (type, html-regex, slug-capture-regex-or-None). Medium confidence — HTML can
# lie ("Greenhouse Gas" copy, a "Workday" job title), so GH/Lever/Ashby get
# actively confirmed. ORDER MATTERS: most-specific first (Radancy/Phenom embed
# stray Workday URLs, so their signatures must precede Workday's generic one).
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
    ("gem", r"jobs\.gem\.com", None),
    ("workable", r"apply\.workable\.com", None),
    ("yello", r"yello\.co|recsolu\.com", None),
    ("zohorecruit", r"\.zohorecruit\.(com|ca|eu|in|com\.au|jp)", None),
]

OTHER_ATS = [
    ("IBM/Infinite Brassring (Kenexa)", r"brassring\.com|kenexa"),
    ("Cornerstone OnDemand", r"\.csod\.com|cornerstoneondemand"),
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

# White-labeled GH/Lever/Ashby boards pin their real slug in this config field.
OVERRIDE_FIELD = {"greenhouse": "token", "lever": "company", "ashby": "board"}
