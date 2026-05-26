from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AnnualReportParsed:
    issuer: str = ""
    year: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ProspectusParsed:
    issuer: str = ""
    data_by_year: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)
    notes: str = ""

