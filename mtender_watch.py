#!/usr/bin/env python3
"""
mtender_watch.py — monitor pentru achiziții publice importante din Moldova.

Parcurge feed-ul OCDS al MTender (public.mtender.gov.md), reține doar
procedurile peste pragurile valorice configurate și le împarte în două
secțiuni: LICITAȚII NOI și CONTRACTE ATRIBUITE / FINALIZATE.

Rulare:  python3 mtender_watch.py [--lookback-days 2] [--out digest.md]
Fără dependențe externe (doar stdlib). Compatibil GitHub Actions.

Feed-ul e un cursor cronologic (oldest-first) paginat prin `offset`
(timestamp); pornim de la now-lookback și mergem înainte până la coadă.
Data din feed e data ULTIMEI MODIFICĂRI, deci prinde și atribuirile
(schimbările de status), nu doar anunțurile noi.
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://public.mtender.gov.md"
PORTAL = "https://mtender.gov.md/tenders"
UA = "mtender-watch/1.0 (monitorizare jurnalistica; contact in repo)"

# ---------------------------------------------------------------- config ---
DEFAULTS = {
    # praguri "importanță" (MDL). Tot ce e sub ambele praguri se ignoră.
    "min_value_mdl": 2_000_000,        # bunuri & servicii
    "min_value_works_mdl": 5_000_000,  # lucrări (mainProcurementCategory=works)
    # instituții urmărite indiferent de valoare (substring, case-insensitive).
    # ex.: ["moldatsa", "agenția proprietății publice", "ansa"]
    "watch_buyers": [],
    # prefixe CPV urmărite indiferent de valoare. ex.: ["3361"] (medicamente)
    "watch_cpv": [],
    "lookback_days": 2,
    "max_pages": 60,          # 60 pagini x 100 = 6000 înregistrări / rulare
    "request_pause": 0.15,    # politețe între request-uri de detaliu
    "state_file": "data/seen.json",
    "out_file": "output/digest_achizitii.md",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    p = Path("config.json")
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception as e:  # config stricat nu oprește rularea
            print(f"[warn] config.json ignorat: {e}", file=sys.stderr)
    return cfg


# ------------------------------------------------------------------ http ---
def get_json(path: str, retries: int = 3) -> dict:
    url = f"{BASE}{path}"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8", "replace")
            if not text.strip():
                return {"data": []}  # margine de cursor: corp gol ocazional
            return json.loads(text)
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"MTender API: {url} -> {last}")


# ------------------------------------------------------------------ feed ---
def walk_feed(lookback_days: int, max_pages: int) -> list[dict]:
    """Toate intrările (ocid, date) modificate în fereastra de lookback."""
    seed = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    offset, out = seed, []
    for _ in range(max_pages):
        qs = f"?offset={urllib.parse.quote(offset)}" if offset else ""
        page = get_json(f"/tenders{qs}")
        data = page.get("data") or []
        out.extend(e for e in data if e.get("ocid"))
        nxt = page.get("offset")
        if not nxt or len(data) < 100:
            break
        offset = nxt
    return out


def compiled_release(ocid: str) -> dict | None:
    pkg = get_json(f"/tenders/{urllib.parse.quote(ocid)}")
    recs = pkg.get("records") or []
    return (recs[0] or {}).get("compiledRelease") if recs else None


# ------------------------------------------------------------ clasificare ---
def shape(cr: dict) -> dict:
    t = cr.get("tender") or {}
    val = t.get("value") or ((cr.get("planning") or {}).get("budget") or {}).get("amount") or {}
    suppliers = []
    for a in cr.get("awards") or []:
        for s in a.get("suppliers") or []:
            if s.get("name"):
                suppliers.append(s["name"])
    award_total = sum(
        (a.get("value") or {}).get("amount") or 0
        for a in cr.get("awards") or []
        if (a.get("status") in (None, "active"))
    )
    return {
        "ocid": cr.get("ocid"),
        "title": t.get("title") or "(fără titlu)",
        "buyer": (t.get("procuringEntity") or {}).get("name")
                 or (cr.get("buyer") or {}).get("name") or "?",
        "amount": val.get("amount"),
        "currency": val.get("currency") or "MDL",
        "status": t.get("status"),
        "status_details": t.get("statusDetails"),
        "category": t.get("mainProcurementCategory"),
        "method": t.get("procurementMethodDetails") or t.get("procurementMethod"),
        "cpv": (t.get("classification") or {}).get("id"),
        "cpv_desc": (t.get("classification") or {}).get("description"),
        "deadline": (t.get("tenderPeriod") or {}).get("endDate"),
        "suppliers": sorted(set(suppliers)),
        "award_total": award_total or None,
        "modified": cr.get("date"),
        "url": f"{PORTAL}/{cr.get('ocid')}",
    }


def is_important(item: dict, cfg: dict) -> bool:
    buyer = (item["buyer"] or "").lower()
    if any(w.lower() in buyer for w in cfg["watch_buyers"]):
        return True
    cpv = item["cpv"] or ""
    if any(cpv.startswith(p) for p in cfg["watch_cpv"]):
        return True
    amount = item["amount"] or item["award_total"] or 0
    threshold = cfg["min_value_works_mdl"] if item["category"] == "works" else cfg["min_value_mdl"]
    return amount >= threshold


def stage(item: dict) -> str:
    """'awarded' dacă există câștigători/atribuire, altfel 'tender' dacă e activă."""
    if item["suppliers"] or item["award_total"] or item["status"] in ("complete",):
        return "awarded"
    if item["status"] in ("active", "planning", "planned") or item["status_details"]:
        return "tender"
    return "other"


# ------------------------------------------------------------------ state ---
def load_seen(path: str) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_seen(path: str, seen: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")


# ----------------------------------------------------------------- digest ---
def fmt_amount(item: dict) -> str:
    a = item["award_total"] or item["amount"]
    if not a:
        return "valoare nedeclarată"
    if a >= 1_000_000:
        return f"{a/1_000_000:,.1f} mln {item['currency']}".replace(",", " ")
    return f"{a:,.0f} {item['currency']}".replace(",", " ")


def render(new_tenders: list, awarded: list, errors: list, cfg: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# Achiziții publice importante — {now}",
         f"_Praguri: ≥{cfg['min_value_mdl']:,} MDL bunuri/servicii, "
         f"≥{cfg['min_value_works_mdl']:,} MDL lucrări._".replace(",", " "), ""]
    L.append(f"## 🏆 Contracte atribuite / finalizate ({len(awarded)})\n")
    if not awarded:
        L.append("_Nimic peste praguri în fereastra analizată._\n")
    for it in awarded:
        who = "; ".join(it["suppliers"]) or "câștigător neindicat în OCDS"
        L.append(f"- **{fmt_amount(it)}** — {it['title']}  \n"
                 f"  Autoritate: {it['buyer']} · Câștigător: **{who}** · "
                 f"{it['method'] or ''} · CPV {it['cpv'] or '?'}  \n"
                 f"  [{it['ocid']}]({it['url']})")
    L.append(f"\n## 📣 Licitații noi / în derulare ({len(new_tenders)})\n")
    if not new_tenders:
        L.append("_Nimic peste praguri în fereastra analizată._\n")
    for it in new_tenders:
        dl = (it["deadline"] or "")[:10]
        L.append(f"- **{fmt_amount(it)}** — {it['title']}  \n"
                 f"  Autoritate: {it['buyer']} · {it['method'] or ''} · "
                 f"CPV {it['cpv'] or '?'} · Termen oferte: {dl or '?'}  \n"
                 f"  [{it['ocid']}]({it['url']})")
    if errors:
        L.append(f"\n## ⚠️ Erori la fetch ({len(errors)})\n")
        L.extend(f"- {e}" for e in errors[:20])
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------- main ---
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int)
    ap.add_argument("--out")
    args = ap.parse_args()

    cfg = load_config()
    if args.lookback_days:
        cfg["lookback_days"] = args.lookback_days
    if args.out:
        cfg["out_file"] = args.out

    feed = walk_feed(cfg["lookback_days"], cfg["max_pages"])
    print(f"[info] {len(feed)} înregistrări în ultimele {cfg['lookback_days']} zile")

    seen = load_seen(cfg["state_file"])
    new_tenders, awarded, errors = [], [], []

    for entry in feed:
        ocid = entry["ocid"]
        try:
            cr = compiled_release(ocid)
            time.sleep(cfg["request_pause"])
            if not cr:
                continue
            item = shape(cr)
            if not is_important(item, cfg):
                continue
            st = stage(item)
            key = f"{ocid}:{st}"          # dedup pe (procedură, etapă):
            if seen.get(key):             # o atribuire după o licitație deja
                continue                  # semnalată apare totuși în digest
            seen[key] = item["modified"] or "1"
            if st == "awarded":
                awarded.append(item)
            elif st == "tender":
                new_tenders.append(item)
        except Exception as e:
            errors.append(f"{ocid}: {e}")

    for lst in (new_tenders, awarded):
        lst.sort(key=lambda x: (x["award_total"] or x["amount"] or 0), reverse=True)

    out = Path(cfg["out_file"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(new_tenders, awarded, errors, cfg), encoding="utf-8")
    save_seen(cfg["state_file"], seen)
    print(f"[ok] {len(awarded)} atribuiri, {len(new_tenders)} licitatii noi -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
