from __future__ import annotations

import os
import re
from typing import Any, Dict, List
from urllib.parse import urlparse


class LinkSafetyModule:
    def __init__(self) -> None:
        self.enabled = os.getenv("LINK_SAFETY_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.mode = os.getenv("LINK_SAFETY_MODE", "warn").strip().lower()
        self.reputation_endpoint = os.getenv("LINK_REPUTATION_API_ENDPOINT", "")
        self.reputation_key = os.getenv("LINK_REPUTATION_API_KEY", "")
        self.bad_tlds = {"zip", "top", "click", "work", "gq", "tk"}
        self.login_words = ("login", "signin", "verify", "secure", "account", "wallet", "password")

    def extract_urls(self, text: str) -> List[str]:
        from core.utils.llm_sanitize import sanitize_llm_value
        text = sanitize_llm_value(text)
        return re.findall(r"https?://[^\s]+", text or "", flags=re.IGNORECASE)

    def _has_mixed_script(self, host: str) -> bool:
        has_lat = bool(re.search(r"[a-zA-Z]", host))
        has_cyr = bool(re.search(r"[а-яА-ЯёЁ]", host))
        return has_lat and has_cyr

    def classify(self, url: str) -> Dict[str, Any]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip(".")
        issues: List[str] = []
        if "xn--" in host:
            issues.append("punycode")
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
            issues.append("ip_url")
        if host.count(".") >= 4:
            issues.append("excessive_subdomains")
        tld = host.rsplit(".", 1)[-1].lower() if "." in host else ""
        if tld in self.bad_tlds:
            issues.append("suspicious_tld")
        if self._has_mixed_script(host):
            issues.append("mixed_scripts")
        path_q = f"{parsed.path}?{parsed.query}".lower()
        if any(w in path_q for w in self.login_words):
            issues.append("fake_login_pattern")
        status = "safe"
        if len(issues) >= 3:
            status = "dangerous"
        elif issues:
            status = "suspicious"
        return {"url": url, "host": host, "status": status, "issues": issues}

    def check_text(self, text: str) -> Dict[str, Any]:
        from core.utils.llm_sanitize import sanitize_llm_value
        text = sanitize_llm_value(text)
        if not self.enabled:
            return {"enabled": False, "links": [], "worst": "safe"}
        links = [self.classify(u) for u in self.extract_urls(text)]
        rank = {"safe": 0, "suspicious": 1, "dangerous": 2}
        worst = "safe"
        for item in links:
            if rank[item["status"]] > rank[worst]:
                worst = item["status"]
        return {"enabled": True, "links": links, "worst": worst, "mode": self.mode}
