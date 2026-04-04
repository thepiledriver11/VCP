"""
Notifier — sends VCP scan results via Slack or generic webhook.
"""

import json
import logging
from typing import List
import requests

from config import Config

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        self.cfg = Config()

    def send_results(self, results: list):
        """Send a batch of VCP scan results."""
        if not results:
            return

        message = self._format_message(results)

        if self.cfg.SLACK_TOKEN:
            self._send_slack(message, results)

        if self.cfg.WEBHOOK_URL:
            self._send_webhook(results)

    def send_breakout_alert(self, ticker: str, breakout, vcp, rs_rating: int):
        """Send an urgent breakout alert."""
        text = (
            f"🚨 *VCP BREAKOUT: {ticker}*\n"
            f"Entry: `{breakout.entry_price:.4f}` | "
            f"Stop: `{breakout.stop_price:.4f}` | "
            f"Risk: `{breakout.risk_pct:.1f}%`\n"
            f"Volume: `{breakout.vol_ratio:.0%}` of 50d avg\n"
            f"RS Rating: `{rs_rating}`\n"
            f"VCP Quality: `{vcp.quality_score:.0f}/100` | "
            f"Contractions: `{vcp.contraction_count}` | "
            f"Base depth: `{vcp.base_depth_pct:.1f}%`"
        )

        if self.cfg.SLACK_TOKEN:
            self._send_slack_raw(text)

        if self.cfg.WEBHOOK_URL:
            self._send_webhook([{
                "type": "breakout",
                "ticker": ticker,
                "entry_price": breakout.entry_price,
                "stop_price": breakout.stop_price,
                "risk_pct": breakout.risk_pct,
                "vol_ratio": breakout.vol_ratio,
                "rs_rating": rs_rating,
                "vcp_quality": vcp.quality_score,
            }])

    def _format_message(self, results: list) -> str:
        lines = [f"📊 *VCP Scan — {len(results)} candidates*\n"]
        for r in sorted(results, key=lambda x: x.get("quality_score", 0), reverse=True):
            ticker = r["ticker"]
            pivot = r.get("pivot_price", "N/A")
            stop = r.get("stop_price", "N/A")
            rs = r.get("rs_rating", "N/A")
            quality = r.get("quality_score", 0)
            depth = r.get("base_depth_pct", "N/A")
            contractions = r.get("contraction_count", "N/A")
            approaching = "🎯 APPROACHING PIVOT" if r.get("approaching_pivot") else ""

            lines.append(
                f"*{ticker}* {approaching}\n"
                f"  Pivot: `{pivot}` | Stop: `{stop}` | RS: `{rs}`\n"
                f"  Quality: `{quality}/100` | "
                f"Contractions: `{contractions}` | "
                f"Base depth: `{depth}%`"
            )
        return "\n\n".join(lines)

    def _send_slack(self, text: str, results: list):
        try:
            resp = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.cfg.SLACK_TOKEN}"},
                json={
                    "channel": self.cfg.SLACK_CHANNEL,
                    "text": text,
                    "mrkdwn": True,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                log.error(f"Slack error: {data.get('error')}")
        except Exception as e:
            log.error(f"Failed to send Slack message: {e}")

    def _send_slack_raw(self, text: str):
        self._send_slack(text, [])

    def _send_webhook(self, results: list):
        try:
            resp = requests.post(
                self.cfg.WEBHOOK_URL,
                json={"event": "vcp_scan", "results": results},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            log.error(f"Failed to send webhook: {e}")
