"""ATS board adapters — one `fetch_<platform>(board)` per ATS type.

ADAPTERS is the registry: config.json's `type` selects the fetcher, and its
insertion order is the canonical group order probe --add writes config.json in.
"""

from adapters.ashby import fetch_ashby
from adapters.avature import fetch_avature
from adapters.bamboohr import fetch_bamboohr
from adapters.dayforce import fetch_dayforce
from adapters.eightfold import fetch_eightfold
from adapters.greenhouse import fetch_greenhouse
from adapters.icims import fetch_icims
from adapters.jazzhr import fetch_jazzhr
from adapters.jobvite import fetch_jobvite
from adapters.lever import fetch_lever
from adapters.oracle import fetch_oracle
from adapters.phenom import fetch_phenom
from adapters.radancy import fetch_radancy
from adapters.ripplematch import fetch_ripplematch
from adapters.rippling import fetch_rippling
from adapters.smartrecruiters import fetch_smartrecruiters
from adapters.successfactors import fetch_successfactors
from adapters.ukg import fetch_ukg
from adapters.workday import fetch_workday
from adapters.gem import fetch_gem
from adapters.workable import fetch_workable
from adapters.yello import fetch_yello
from adapters.zohorecruit import fetch_zohorecruit

ADAPTERS = {
    "workday": fetch_workday,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "phenom": fetch_phenom,
    "successfactors": fetch_successfactors,
    "oracle": fetch_oracle,
    "radancy": fetch_radancy,
    "smartrecruiters": fetch_smartrecruiters,
    "bamboohr": fetch_bamboohr,
    "rippling": fetch_rippling,
    "ripplematch": fetch_ripplematch,
    "ukg": fetch_ukg,
    "dayforce": fetch_dayforce,
    "icims": fetch_icims,
    "jazzhr": fetch_jazzhr,
    "jobvite": fetch_jobvite,
    "eightfold": fetch_eightfold,
    "avature": fetch_avature,
    "gem": fetch_gem,
    "workable": fetch_workable,
    "yello": fetch_yello,
    "zohorecruit": fetch_zohorecruit,
}
