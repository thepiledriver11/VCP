"""
IG Markets REST API client.
Handles authentication, epic search, and watchlist management.
"""

import logging
import requests
from typing import Optional
from config import Config

log = logging.getLogger(__name__)


class IGClient:
    def __init__(self):
        self.cfg = Config()
        self.base_url = self.cfg.ig_base_url()
        self.session = requests.Session()
        self.cst: Optional[str] = None
        self.x_security_token: Optional[str] = None
        self._authenticated = False

    def authenticate(self) -> bool:
        if not all([self.cfg.IG_API_KEY, self.cfg.IG_USERNAME, self.cfg.IG_PASSWORD]):
            log.error("IG credentials not set — add IG_API_KEY, IG_USERNAME, IG_PASSWORD to Railway env vars")
            return False
        try:
            resp = self.session.post(
                f"{self.base_url}/session",
                headers={
                    "Content-Type": "application/json; charset=UTF-8",
                    "Accept": "application/json; charset=UTF-8",
                    "X-IG-API-KEY": self.cfg.IG_API_KEY,
                    "Version": "2",
                },
                json={
                    "identifier": self.cfg.IG_USERNAME,
                    "password": self.cfg.IG_PASSWORD,
                    "encryptedPassword": False,
                },
                timeout=15,
            )
            resp.raise_for_status()
            self.cst = resp.headers.get("CST")
            self.x_security_token = resp.headers.get("X-SECURITY-TOKEN")
            self._authenticated = bool(self.cst and self.x_security_token)
            if self._authenticated:
                log.info("IG authentication successful")
            else:
                log.error("IG auth succeeded but tokens missing from response")
            return self._authenticated
        except Exception as e:
            log.error(f"IG authentication failed: {e}")
            return False

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "X-IG-API-KEY": self.cfg.IG_API_KEY or "",
            "CST": self.cst or "",
            "X-SECURITY-TOKEN": self.x_security_token or "",
        }

    def search_epic(self, ticker: str) -> Optional[str]:
        """Resolve ticker to IG epic. e.g. 'BHP.AX' → IG epic string."""
        search_term = ticker.replace(".AX", "").replace("-", ".")
        try:
            resp = self.session.get(
                f"{self.base_url}/markets",
                headers={**self._headers(), "Version": "1"},
                params={"searchTerm": search_term},
                timeout=15,
            )
            resp.raise_for_status()
            markets = resp.json().get("markets", [])
            if not markets:
                log.warning(f"No IG markets found for {ticker}")
                return None

            is_asx = ticker.endswith(".AX")
            for m in markets:
                epic = m.get("epic", "")
                name = m.get("instrumentName", "").upper()
                inst_type = m.get("instrumentType", "")
                if search_term.upper() in name and inst_type in ("SHARES",):
                    if is_asx and any(x in epic for x in ("ASX", "AUS", ".AU.")):
                        log.info(f"Resolved {ticker} → {epic}")
                        return epic
                    elif not is_asx and any(x in epic for x in ("US", "NASDAQ", "NYSE")):
                        log.info(f"Resolved {ticker} → {epic}")
                        return epic

            epic = markets[0].get("epic")
            log.info(f"Resolved {ticker} → {epic} (first result)")
            return epic
        except Exception as e:
            log.error(f"Epic search failed for {ticker}: {e}")
            return None

    def get_watchlists(self) -> list:
        try:
            resp = self.session.get(
                f"{self.base_url}/watchlists",
                headers={**self._headers(), "Version": "1"},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("watchlists", [])
        except Exception as e:
            log.error(f"Failed to get watchlists: {e}")
            return []

    def find_or_create_watchlist(self, name: str = "VCP Setups") -> Optional[str]:
        for wl in self.get_watchlists():
            if wl.get("name", "").lower() == name.lower():
                log.info(f"Found watchlist '{name}' id={wl['id']}")
                return wl["id"]
        try:
            resp = self.session.post(
                f"{self.base_url}/watchlists",
                headers={**self._headers(), "Version": "1"},
                json={"name": name, "epics": []},
                timeout=15,
            )
            resp.raise_for_status()
            wl_id = resp.json().get("watchlistId")
            log.info(f"Created watchlist '{name}' id={wl_id}")
            return wl_id
        except Exception as e:
            log.error(f"Failed to create watchlist '{name}': {e}")
            return None

    def add_to_watchlist(self, watchlist_id: str, epic: str) -> bool:
        try:
            resp = self.session.put(
                f"{self.base_url}/watchlists/{watchlist_id}",
                headers={**self._headers(), "Version": "1"},
                json={"epic": epic},
                timeout=15,
            )
            return resp.status_code in (200, 208)
        except Exception as e:
            log.error(f"Failed to add {epic}: {e}")
            return False

    def remove_from_watchlist(self, watchlist_id: str, epic: str) -> bool:
        try:
            resp = self.session.delete(
                f"{self.base_url}/watchlists/{watchlist_id}/{epic}",
                headers={**self._headers(), "Version": "1"},
                timeout=15,
            )
            return resp.status_code in (200, 204)
        except Exception as e:
            log.error(f"Failed to remove {epic}: {e}")
            return False

    def clear_watchlist(self, watchlist_id: str):
        try:
            resp = self.session.get(
                f"{self.base_url}/watchlists/{watchlist_id}",
                headers={**self._headers(), "Version": "1"},
                timeout=15,
            )
            resp.raise_for_status()
            for m in resp.json().get("markets", []):
                epic = m.get("epic")
                if epic:
                    self.remove_from_watchlist(watchlist_id, epic)
        except Exception as e:
            log.error(f"Failed to clear watchlist: {e}")

    def sync_watchlist(self, tickers: list, watchlist_name: str = "VCP Setups", replace: bool = True) -> dict:
        """Full sync: authenticate → find/create watchlist → clear → add all tickers."""
        if not self._authenticated:
            if not self.authenticate():
                return {"error": "Authentication failed", "added": 0, "failed": len(tickers)}

        wl_id = self.find_or_create_watchlist(watchlist_name)
        if not wl_id:
            return {"error": "Could not find/create watchlist", "added": 0, "failed": len(tickers)}

        if replace:
            self.clear_watchlist(wl_id)

        results = {"added": 0, "failed": 0, "watchlist": watchlist_name, "details": []}
        for ticker in tickers:
            epic = self.search_epic(ticker)
            if not epic:
                results["failed"] += 1
                results["details"].append({"ticker": ticker, "status": "epic_not_found"})
                continue
            ok = self.add_to_watchlist(wl_id, epic)
            if ok:
                results["added"] += 1
                results["details"].append({"ticker": ticker, "epic": epic, "status": "added"})
            else:
                results["failed"] += 1
                results["details"].append({"ticker": ticker, "epic": epic, "status": "failed"})

        log.info(f"Watchlist sync done — added={results['added']} failed={results['failed']}")
        return results
