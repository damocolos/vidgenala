"""Smoke test for AI providers: one tiny real request each, to prove the key, URL and model work.

    python scripts/smoke_llm.py                  # every provider that has a key (saved in the app, or in its env var)
    python scripts/smoke_llm.py deepseek         # just one
    python scripts/smoke_llm.py --models deepseek   # also list the model ids the provider offers

Costs a few tokens per provider. Keys are read from the app database (decrypted in memory) or from
OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY; they are never printed.
Exit code: 0 all tested providers answered, 1 at least one failed, 2 nothing could be tested.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SYSTEM = 'Reply with exactly this JSON and nothing else: {"status":"ok"}'
USER = "ping"


def run_one(prov: dict, chat=None, log=lambda m: None) -> dict:
    """One tiny request against one provider. `chat` is injectable for tests."""
    from nalarka.core import llm
    chat = chat or llm._chat_text_impl
    model = (prov.get("script_model") or prov.get("coder_model") or "").strip()
    out = {"provider": prov["key"], "model": model, "ok": False, "seconds": None, "tokens": None, "error": None, "reply": ""}
    if not model:
        out["error"] = "no model is configured for this provider"
        return out
    llm._TLS.usage = None
    t0 = time.time()
    try:
        text, finish = chat(prov["base_url"], prov.get("api_key") or "", model, SYSTEM, USER, log, "SMOKE",
                            temperature=0.0, timeout=60.0, max_tokens=64, min_chars=1)
    except Exception as err:   # noqa: BLE001 - report, never crash the whole run
        out["error"] = llm.friendly_api_error(err, prov["base_url"]) if not isinstance(err, RuntimeError) else str(err)
        out["seconds"] = round(time.time() - t0, 2)
        return out
    out["seconds"] = round(time.time() - t0, 2)
    out["reply"] = (text or "").strip()[:80]
    out["tokens"] = getattr(llm._TLS, "usage", None)           # (prompt, completion) when the provider reports it
    out["ok"] = bool(out["reply"])
    if not out["ok"]:
        out["error"] = f"empty reply (finish={finish})"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("providers", nargs="*", help="provider keys (default: all that have a key)")
    ap.add_argument("--models", action="store_true", help="also list model ids each provider offers")
    args = ap.parse_args(argv)

    from nalarka.core import llm
    from nalarka.db.connection import get_connection, run_migrations
    from nalarka.db.seed import seed_defaults
    from nalarka.db.repos.settings import list_providers

    conn = get_connection(os.environ.get("NALARKA_DB_PATH") or None)
    run_migrations(conn)
    seed_defaults(conn)
    provs = [p for p in list_providers(conn) if not args.providers or p["key"] in args.providers]
    if args.providers and len(provs) != len(set(args.providers)):
        print("Unknown provider key(s): " + ", ".join(sorted(set(args.providers) - {p["key"] for p in provs})), file=sys.stderr)
        return 2

    tested = failed = 0
    for p in provs:
        has_key = bool(p.get("api_key")) or bool(os.environ.get(llm._env_key_name(p["base_url"]), ""))
        if not has_key:
            print(f"- {p['key']:<11} skipped (no API key saved or in the environment)")
            continue
        tested += 1
        r = run_one(p)
        if r["ok"]:
            tok = f", tokens in/out {r['tokens'][0]}/{r['tokens'][1]}" if r["tokens"] else ", tokens not reported by provider"
            print(f"+ {p['key']:<11} OK  {r['model']}  {r['seconds']}s{tok}  reply={r['reply']!r}")
        else:
            failed += 1
            print(f"x {p['key']:<11} FAILED  {r['model']}  {r['error']}")
        if args.models and r["ok"]:
            try:
                ids = [m["id"] for m in llm.fetch_provider_models(p["base_url"], p.get("api_key") or "", lambda m: None)]
                print(f"    {len(ids)} models, e.g. " + ", ".join(ids[:8]))
            except Exception as err:   # noqa: BLE001
                print(f"    could not list models: {err}")
    if not tested:
        print("Nothing tested: save a key in Settings, or export OPENROUTER_API_KEY / DEEPSEEK_API_KEY.", file=sys.stderr)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
