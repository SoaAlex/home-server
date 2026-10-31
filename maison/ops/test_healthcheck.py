"""Tests du harness de santé (stdlib, sans Docker) : `python3 -m unittest discover -s ops -v` (depuis maison/)."""
import copy
import unittest

import healthcheck as hc

NOW = 1_800_000_000.0
H = 3600


def green_facts():
    return {
        "now": NOW, "backend": "local", "cli": "/x/maison", "cli_exists": True,
        "containers": {n: {"running": True, "status": "running", "restarting": False, "restart_count": 0,
                           "started_at": NOW - 48 * H, "health": None, "dns": list(hc.EXPECTED_DNS)}
                       for n in hc.CONTAINERS},
        "api": {"criteria": 200},
        "relay": {t: {"ok": True, "detail": "", "secs": 0.3} for t in hc.RELAY_TESTS},
        "collector_log": {"last_cycle_end": NOW - 0.5 * H, "source_errors": {}, "email_errors": 0,
                          "cycle_errors": 0, "zero_sources": {}, "postprocess_warnings": 0, "email_runs": 15},
        "web_log": {"tools_total": 40, "tools_5xx": 0, "last_tools_status": 200, "enrich_post_errors": 0,
                    "pending_errors": 0, "server_5xx": 0, "tracebacks": 0},
        "db": {"sources": [{"source": "bienici", "active": 600, "last_seen_age_h": 0.6},
                           {"source": "seloger", "active": 600, "last_seen_age_h": 5},
                           {"source": "pap", "active": 0, "last_seen_age_h": None}],
               "queue": [{"id": 1, "source": "seloger", "inv": False, "age_h": 3, "req_age_h": None}],
               "enriched_24h": 20, "failed_24h": [], "no_summary_24h": [], "new_24h": 40},
        "jobs": {n: {"id": n, "enabled": True, "state": "scheduled", "paused_reason": None, "last_status": "ok",
                     "last_error": None, "last_run_age_h": 9.5, "completed_24h": 8, "failed_24h": 0,
                     "errors_24h": [], "empty_24h": 0, "limit_24h": 0} for n in hc.JOBS},
        "disk_free": 40 * 1024**3,
    }


def prev_state(**kw):
    s = {"last_check": NOW - 24 * H, "pending_first_seen": {}, "restart_counts": {n: 0 for n in hc.CONTAINERS},
         "queue_standard": 1}
    s.update(kw)
    return s


def run(f, state=None):
    return hc.evaluate(f, prev_state() if state is None else state)


class Green(unittest.TestCase):
    def test_all_green_is_one_ok_line(self):
        P, W, S, _ = run(green_facts())
        self.assertEqual((P, W), ([], []))
        msg = hc.render(P, W, S)
        self.assertTrue(msg.startswith("✅"))
        self.assertEqual(msg.count("\n"), 0)

    def test_first_check_sets_baseline_without_alert(self):
        f = green_facts()
        f["db"]["queue"][0]["age_h"] = 500        # vieille annonce, mais aucune observation antérieure
        P, W, S, st = hc.evaluate(f, {})
        self.assertEqual(P, [])
        self.assertIn("1", st["pending_first_seen"])
        self.assertIn("référence", hc.render(P, W, S))


class StuckAnalyses(unittest.TestCase):
    def test_listing_still_pending_since_previous_check_is_blocked(self):
        P, _, _, st = run(green_facts(), prev_state(pending_first_seen={"1": NOW - 24 * H}))
        self.assertTrue(any("bloquée" in p and "#1" in p for p in P), P)
        self.assertEqual(st["pending_first_seen"]["1"], NOW - 24 * H)   # date d'origine conservée

    def test_recently_seen_pending_is_fine(self):
        P, _, _, _ = run(green_facts(), prev_state(pending_first_seen={"1": NOW - 10 * H}))
        self.assertEqual(P, [])

    def test_listing_leaving_queue_is_forgotten(self):
        f = green_facts()
        f["db"]["queue"] = []
        _, _, _, st = run(f, prev_state(pending_first_seen={"1": NOW - 30 * H}))
        self.assertEqual(st["pending_first_seen"], {})

    def test_old_investigation_request(self):
        f = green_facts()
        f["db"]["queue"].append({"id": 7, "source": "bienici", "inv": True, "age_h": 400, "req_age_h": 13})
        P, _, _, _ = run(f)
        self.assertTrue(any("investigation" in p and "#7" in p for p in P), P)

    def test_fresh_investigation_request_is_fine(self):
        f = green_facts()
        f["db"]["queue"].append({"id": 7, "source": "bienici", "inv": True, "age_h": 400, "req_age_h": 1})
        self.assertEqual(run(f)[0], [])

    def test_investigation_not_counted_as_standard_stuck(self):
        f = green_facts()
        f["db"]["queue"] = [{"id": 7, "source": "bienici", "inv": True, "age_h": 400, "req_age_h": 1}]
        P, _, _, st = run(f, prev_state(pending_first_seen={"7": NOW - 30 * H}))
        self.assertEqual(P, [])
        self.assertNotIn("7", st["pending_first_seen"])

    def test_enrichment_standstill(self):
        f = green_facts()
        f["db"]["enriched_24h"] = 0
        P, _, _, _ = run(f, prev_state(queue_standard=5))
        self.assertTrue(any("à l'arrêt" in p for p in P), P)

    def test_failed_listings_reported(self):
        f = green_facts()
        f["db"]["failed_24h"] = [42]
        self.assertTrue(any("#42" in p for p in run(f)[0]))

    def test_backlog_warning(self):
        f = green_facts()
        f["db"]["queue"] = [{"id": i, "source": "s", "inv": False, "age_h": 1, "req_age_h": None} for i in range(60)]
        _, W, _, _ = run(f)
        self.assertTrue(any("chargée" in w for w in W), W)


class Errors(unittest.TestCase):
    def test_dead_sweep_source_is_a_problem(self):
        f = green_facts()
        f["db"]["sources"][0]["last_seen_age_h"] = 120     # Bien'ici 26 -> 31/10/2026
        P, _, _, _ = run(f)
        self.assertTrue(any("bienici" in p for p in P), P)

    def test_quiet_mail_source_is_only_a_warning(self):
        f = green_facts()
        f["db"]["sources"][1]["last_seen_age_h"] = 60
        P, W, _, _ = run(f)
        self.assertEqual(P, [])
        self.assertTrue(any("seloger" in w for w in W))

    def test_source_without_active_listing_ignored(self):
        f = green_facts()
        f["db"]["sources"][2]["last_seen_age_h"] = 999
        self.assertEqual(run(f)[:2], ([], []))

    def test_relay_down(self):
        f = green_facts()
        f["relay"]["ademe"] = {"ok": False, "detail": "HTTP 500: Temporary failure in name resolution", "secs": 1}
        P, _, _, _ = run(f)
        self.assertTrue(any("relais en panne" in p and "ademe" in p for p in P), P)

    def test_container_down_and_dns(self):
        f = green_facts()
        f["containers"]["maison-collector"].update(running=False, status="exited")
        f["containers"]["maison-web"]["dns"] = []
        P, W, _, _ = run(f)
        self.assertTrue(any("maison-collector" in p and "exited" in p for p in P), P)
        self.assertTrue(any("DNS" in w for w in W), W)

    def test_unexpected_restart(self):
        f = green_facts()
        f["containers"]["maison-web"]["restart_count"] = 3
        _, W, _, st = run(f)
        self.assertTrue(any("redémarré" in w for w in W), W)
        self.assertEqual(st["restart_counts"]["maison-web"], 3)

    def test_collector_cycle_late(self):
        f = green_facts()
        f["collector_log"]["last_cycle_end"] = NOW - 5 * H
        self.assertTrue(any("cycle" in p for p in run(f)[0]))

    def test_collector_just_restarted_is_fine(self):
        f = green_facts()
        f["collector_log"]["last_cycle_end"] = None
        f["containers"]["maison-collector"]["started_at"] = NOW - 0.3 * H
        P, W, S, _ = run(f)
        self.assertEqual(P, [])
        self.assertIn("1er cycle en cours", hc.render(P, W, S))

    def test_collector_source_errors(self):
        f = green_facts()
        f["collector_log"]["source_errors"] = {"bienici": 14}
        f["collector_log"]["email_errors"] = 2
        P, _, _, _ = run(f)
        self.assertTrue(any("bienici" in p for p in P))
        self.assertTrue(any("e-mails" in p for p in P))

    def test_relay_5xx_in_logs(self):
        f = green_facts()
        f["web_log"].update(tools_5xx=2, last_tools_status=200)
        P, W, _, _ = run(f)
        self.assertEqual(P, [])
        self.assertTrue(any("5xx" in w for w in W))
        f["web_log"].update(tools_5xx=12)
        self.assertTrue(any("5xx" in p for p in run(f)[0]))

    def test_enrichment_post_refused(self):
        f = green_facts()
        f["web_log"]["enrich_post_errors"] = 1
        self.assertTrue(any("POST" in p for p in run(f)[0]))

    def test_job_paused(self):
        f = green_facts()
        f["jobs"]["maison-enrichment"].update(enabled=False, state="paused", paused_reason="test")
        self.assertTrue(any("en pause" in p for p in run(f)[0]))

    def test_job_missing_or_late(self):
        f = green_facts()
        f["jobs"]["maison-address-investigation"] = None
        f["jobs"]["maison-enrichment"]["last_run_age_h"] = 40
        P, _, _, _ = run(f)
        self.assertTrue(any("introuvable" in p for p in P))
        self.assertTrue(any("dernier tir" in p for p in P))

    def test_job_failures_threshold_and_empty_dedup(self):
        f = green_facts()
        f["jobs"]["maison-enrichment"].update(failed_24h=1, errors_24h=["interrupted"], empty_24h=1)
        P, W, _, _ = run(f)
        self.assertEqual(P, [])
        self.assertEqual(len([w for w in W if "maison-enrichment" in w]), 1, W)   # vide du tir échoué non recompté
        f["jobs"]["maison-enrichment"].update(failed_24h=3, empty_24h=3)
        self.assertTrue(any("en échec" in p for p in run(f)[0]))

    def test_session_limit_runs(self):
        f = green_facts()
        f["jobs"]["maison-enrichment"].update(empty_24h=4)
        self.assertTrue(any("limite de session" in p for p in run(f)[0]))

    def test_db_unreadable(self):
        f = green_facts()
        f["db"] = {"error": "connection refused"}
        self.assertTrue(any("base" in p for p in run(f)[0]))

    def test_render_problem_lists_every_item(self):
        f = green_facts()
        f["api"]["criteria"] = "URLError"
        f["db"]["no_summary_24h"] = [5]
        P, W, S, _ = run(f)
        msg = hc.render(P, W, S)
        self.assertTrue(msg.startswith("🚨"))
        self.assertEqual(msg.count("\n• "), len(P) + len(W))


class Parsers(unittest.TestCase):
    def test_collector_log(self):
        log = "\n".join([
            "2026-10-31T10:00:00.123456789Z 2026-10-31 11:00:00,1 INFO bienici: 615 annonces (0 disparues)",
            "2026-10-31T10:05:00.123456789Z 2026-10-31 11:05:00,1 ERROR source bienici en échec",
            "2026-10-31T10:06:00.1Z 2026-10-31 11:06:00,1 WARNING pap: 0 annonce (source cassée ?)",
            "2026-10-31T10:07:00.1Z 2026-10-31 11:07:00,1 ERROR emails en échec",
            "2026-10-31T10:08:00.1Z 2026-10-31 11:08:00,1 INFO emails: 3 annonces",
            "2026-10-31T10:20:00.500Z 2026-10-31 11:20:00,1 INFO cycle terminé en 1200s",
        ])
        r = hc.parse_collector_log(log)
        self.assertEqual(r["source_errors"], {"bienici": 1})
        self.assertEqual(r["zero_sources"], {"pap": 1})
        self.assertEqual((r["email_errors"], r["email_runs"]), (1, 1))
        self.assertEqual(r["last_cycle_end"], hc.parse_ts("2026-10-31T10:20:00.5Z"))

    def test_web_log(self):
        log = "\n".join([
            '2026-10-31T14:59:50Z INFO:     172.18.0.1:1 - "GET /api/tools/reverse?lon=3.1 HTTP/1.1" 500 Internal',
            '2026-10-31T14:59:51Z INFO:     172.18.0.1:1 - "GET /api/tools/ban?q=x HTTP/1.1" 200 OK',
            '2026-10-31T14:59:52Z INFO:     172.18.0.1:1 - "POST /api/listings/12/enrichment HTTP/1.1" 422 Unprocessable',
            '2026-10-31T14:59:53Z INFO:     1.2.3.4:1 - "GET /photos/1/00.jpg HTTP/1.1" 404 Not Found',
            "Traceback (most recent call last):",
        ])
        r = hc.parse_web_log(log)
        self.assertEqual((r["tools_total"], r["tools_5xx"], r["last_tools_status"]), (2, 1, 200))
        self.assertEqual((r["enrich_post_errors"], r["server_5xx"], r["tracebacks"]), (1, 1, 1))

    def test_parse_ts_formats(self):
        a = hc.parse_ts("2026-10-31T14:44:33.913548705Z")
        b = hc.parse_ts("2026-10-31T15:44:33.913548+01:00")
        assert a is not None and b is not None
        self.assertAlmostEqual(a, b, places=3)
        self.assertIsNone(hc.parse_ts(None))

    def test_last_response(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "o.md"
            p.write_text("# Job\n## Prompt\nblah usage limit\n\n## Response\n\n3 annonces traitées\n")
            self.assertEqual(hc.last_response(p), "3 annonces traitées")
            p.write_text("# Job\n## Prompt\nx\n")
            self.assertIsNone(hc.last_response(p))


class StateIsolation(unittest.TestCase):
    def test_evaluate_does_not_mutate_inputs(self):
        f, s = green_facts(), prev_state(pending_first_seen={"1": NOW - 30 * H})
        f0, s0 = copy.deepcopy(f), copy.deepcopy(s)
        run(f, s)
        self.assertEqual((f, s), (f0, s0))


if __name__ == "__main__":
    unittest.main()
