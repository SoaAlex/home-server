"""Offline tests of the decision logic of nas_boot_check (no Docker, no systemd, no network).

    cd /volume1/docker/boot-check && python3 -m unittest -v
"""
import copy
import unittest

import nas_boot_check as nbc


def healthy():
    exp = {
        "taken": "2026-11-03T09:00:00+01:00",
        "mounts": ["/volume1", "/volume2", "/home"],
        "containers": {"db": {"has_health": False}, "web": {"has_health": True}},
        "stopped_on_purpose": ["old"],
        "postgres": ["db"],
        "ports": {"critical": [[8090, "tcp"], [51820, "udp"]], "critical_extra": [], "other": [[5000, "tcp"]]},
        "http": [{"host": "127.0.0.1", "port": 8090, "path": "/"}],
        "interfaces": {"bridge0": ["192.168.1.55"], "ph-shim": ["192.168.1.56"]},
        "macvlan": [{"container": "dns", "ip": "192.168.1.100", "dev": "ph-shim", "dns": True}],
        "system_active": ["docker.service", "macvlan-shim.service"],
        "system_inactive": ["tailscaled.service"],
        "files": ["/etc/systemd/system/macvlan-shim.service"],
        "failed_baseline": {"system": ["music_serv.service"], "user": []},
        "user_units": {"services": ["hermes-gateway.service"], "timers": ["nas-backup.timer"]},
        "hermes_gateway": True,
        "versions": {"kernel": "6.18.15", "docker": "29.6.2"},
    }
    c = lambda **k: {"policy": "unless-stopped", "status": "running", "running": True, "restarting": False,
                     "health": None, "has_health": False, "exit": 0, "net": "bridge", "image": "x",
                     "ports": [], "networks": {}, **k}
    obs = {
        "now": 0, "uptime": 200.0,
        "mounts": {"/volume1": True, "/volume2": True, "/home": True},
        "disk": {"/volume1": 60}, "raid": {"md1": "U", "md2": "UUUU"},
        "docker": {"db": c(), "web": c(health="healthy", has_health=True),
                   "old": c(running=False, status="exited", exit=0)},
        "tcp": {8090, 5000}, "udp": {51820}, "http": {8090: 200},
        "ifaces": {"bridge0": ["192.168.1.55"], "ph-shim": ["192.168.1.56"]},
        "routes": {"192.168.1.100": "ph-shim"}, "dns": {"192.168.1.100": True},
        "net": {"resolve": True, "connect": True},
        "sys_units": {"docker.service": {"ActiveState": "active"},
                      "macvlan-shim.service": {"ActiveState": "active", "UnitFileState": "enabled"},
                      "tailscaled.service": {"ActiveState": "inactive", "UnitFileState": "disabled"}},
        "failed_sys": {"music_serv.service"}, "failed_user": set(),
        "files": {"/etc/systemd/system/macvlan-shim.service": True},
        "user_manager": True, "linger": True,
        "user_units": {"hermes-gateway.service": {"ActiveState": "active"},
                       "nas-backup.timer": {"ActiveState": "active", "NextElapseUSecRealtime": "Wed 2026-11-04 04:00:00"}},
        "gateway": {"gateway_state": "running", "pid": __import__("os").getpid(),
                    "platforms": {"discord": {"state": "connected"}}, "_age": 30},
        "pg": {"db": True}, "mem_mib": 2000, "ntp": True,
        "versions": {"kernel": "6.18.15", "docker": "29.6.2"},
    }
    return exp, obs


def statuses(checks):
    return {(c.group, c.status) for c in checks}


class Parsers(unittest.TestCase):
    def test_ss_ipv4_and_ipv6(self):
        text = ("LISTEN 0 4096 0.0.0.0:8090 0.0.0.0:*\nLISTEN 0 4096 [::]:8090 [::]:*\n"
                "LISTEN 0 128 127.0.0.1:53 0.0.0.0:*\nUNCONN 0 0 *:51820 *:*\n")
        self.assertEqual(nbc.parse_ss(text), {8090, 53, 51820})

    def test_mdstat(self):
        text = ("Personalities : [raid5]\nmd2 : active raid5 sda2[0] sdd2[3]\n"
                "      5812147200 blocks super 1.2 level 5, 64k chunk, algorithm 2 [4/4] [UUUU]\n"
                "      bitmap: 4/15 pages\n\nmd1 : active raid1 nvme0n1p2[0]\n"
                "      108967936 blocks super 1.2 [1/1] [U]\n")
        self.assertEqual(nbc.parse_mdstat(text), {"md2": "UUUU", "md1": "U"})
        degraded = "md2 : active raid5 sda2[0]\n   100 blocks super 1.2 level 5 [4/3] [UU_U]\n"
        self.assertEqual(nbc.parse_mdstat(degraded), {"md2": "UU_U"})

    def test_duration(self):
        self.assertEqual(nbc.fmt_dur(192), "3 min 12 s")
        self.assertEqual(nbc.fmt_dur(45), "45 s")
        self.assertEqual(nbc.fmt_dur(3 * 86400 + 4 * 3600), "3 j 4 h")


class Evaluate(unittest.TestCase):
    def test_healthy_box_has_no_failure(self):
        exp, obs = healthy()
        checks = nbc.evaluate(exp, obs)
        self.assertFalse([c for c in checks if c.status == nbc.FAIL], nbc.render_cli(checks))
        self.assertFalse([c for c in checks if c.status == nbc.WARN], nbc.render_cli(checks))

    def fails(self, mutate):
        exp, obs = healthy()
        mutate(exp, obs)
        return [c for c in nbc.evaluate(exp, obs) if c.status == nbc.FAIL]

    def test_container_down(self):
        f = self.fails(lambda e, o: o["docker"]["db"].update(running=False, status="exited", exit=137))
        self.assertTrue(any("db : arrêté" in c.text for c in f))

    def test_container_missing(self):
        f = self.fails(lambda e, o: o["docker"].pop("web"))
        self.assertTrue(any("web : absent" in c.text for c in f))

    def test_healthcheck_still_starting(self):
        f = self.fails(lambda e, o: o["docker"]["web"].update(health="starting"))
        self.assertTrue(any("web : starting" in c.text for c in f))

    def test_crash_loop(self):
        f = self.fails(lambda e, o: o["docker"]["db"].update(restarting=True))
        self.assertTrue(any("boucle" in c.text for c in f))

    def test_docker_unreachable(self):
        f = self.fails(lambda e, o: o.update(docker=None))
        self.assertTrue(any(c.group == "docker" for c in f))

    def test_critical_port_missing(self):
        f = self.fails(lambda e, o: o["udp"].discard(51820))
        self.assertTrue(any("51820/udp" in c.text for c in f))

    def test_other_port_missing_is_only_a_warning(self):
        exp, obs = healthy()
        obs["tcp"].discard(5000)
        checks = nbc.evaluate(exp, obs)
        self.assertIn(("ports", nbc.WARN), statuses(checks))
        self.assertFalse([c for c in checks if c.status == nbc.FAIL])

    def test_http_500_or_silence(self):
        self.assertTrue(self.fails(lambda e, o: o["http"].update({8090: 502})))
        self.assertTrue(self.fails(lambda e, o: o["http"].update({8090: None})))
        exp, obs = healthy()
        obs["http"][8090] = 401  # a login wall is an answer
        self.assertFalse([c for c in nbc.evaluate(exp, obs) if c.status == nbc.FAIL])

    def test_shim_gone_or_dns_dead(self):
        self.assertTrue(self.fails(lambda e, o: o["ifaces"].pop("ph-shim")))
        self.assertTrue(self.fails(lambda e, o: o["routes"].update({"192.168.1.100": "bridge0"})))
        self.assertTrue(self.fails(lambda e, o: o["dns"].update({"192.168.1.100": False})))

    def test_no_internet(self):
        self.assertTrue(self.fails(lambda e, o: o["net"].update(resolve=False)))

    def test_tailscale_must_stay_off(self):
        f = self.fails(lambda e, o: o["sys_units"]["tailscaled.service"].update(ActiveState="active"))
        self.assertTrue(any(c.group == "sécurité" for c in f))
        f = self.fails(lambda e, o: o["sys_units"]["tailscaled.service"].update(UnitFileState="enabled"))
        self.assertTrue(any(c.group == "sécurité" for c in f))

    def test_update_wiped_a_root_customisation(self):
        f = self.fails(lambda e, o: o["files"].update({"/etc/systemd/system/macvlan-shim.service": False}))
        self.assertTrue(any("disparus" in c.text for c in f))
        f = self.fails(lambda e, o: o["sys_units"]["macvlan-shim.service"].update(UnitFileState="disabled"))
        self.assertTrue(any("macvlan-shim" in c.text for c in f))

    def test_raid_degraded_and_unmounted(self):
        self.assertTrue(self.fails(lambda e, o: o["raid"].update(md2="UU_U")))
        self.assertTrue(self.fails(lambda e, o: o["mounts"].update({"/home": False})))

    def test_user_services_not_started(self):
        f = self.fails(lambda e, o: o["user_units"]["hermes-gateway.service"].update(ActiveState="inactive"))
        self.assertTrue(any("hermes-gateway" in c.text for c in f))
        f = self.fails(lambda e, o: o["user_units"]["nas-backup.timer"].update(NextElapseUSecRealtime=""))
        self.assertTrue(any("nas-backup.timer" in c.text for c in f))
        self.assertTrue(self.fails(lambda e, o: o.update(user_manager=False)))
        self.assertTrue(self.fails(lambda e, o: o.update(linger=False)))

    def test_hermes_gateway_states(self):
        self.assertTrue(self.fails(lambda e, o: o.update(gateway=None)))
        self.assertTrue(self.fails(lambda e, o: o["gateway"].update(gateway_state="stopped")))
        self.assertTrue(self.fails(lambda e, o: o["gateway"]["platforms"]["discord"].update(state="connecting")))
        self.assertTrue(self.fails(lambda e, o: o["gateway"].update(pid=2 ** 22 + 1)))

    def test_postgres_not_ready(self):
        self.assertTrue(self.fails(lambda e, o: o["pg"].update(db=False)))

    def test_known_failed_units_are_ignored_new_ones_warn(self):
        exp, obs = healthy()
        obs["failed_sys"].add("brand-new.service")
        warns = [c for c in nbc.evaluate(exp, obs) if c.status == nbc.WARN]
        self.assertTrue(any("brand-new.service" in c.text for c in warns))
        self.assertFalse(any("music_serv" in c.text for c in warns))

    def test_update_is_reported_as_information(self):
        exp, obs = healthy()
        obs["versions"] = {"kernel": "6.19.2", "docker": "29.6.2"}
        info = [c for c in nbc.evaluate(exp, obs) if c.status == nbc.INFO]
        self.assertTrue(any("6.18.15 → 6.19.2" in c.text for c in info))

    def test_stray_exited_container_warns_but_stopped_on_purpose_does_not(self):
        exp, obs = healthy()
        obs["docker"]["crashed"] = dict(obs["docker"]["db"], running=False, status="exited", exit=1)
        warns = [c for c in nbc.evaluate(exp, obs) if c.status == nbc.WARN]
        self.assertTrue(any("crashed" in c.text for c in warns))
        self.assertFalse(any("old" in c.text.split() for c in warns))


class Heal(unittest.TestCase):
    def test_only_expected_things_are_started_postgres_first(self):
        exp, obs = healthy()
        exp["containers"]["common-postgres"] = {"has_health": False}
        exp["containers"]["common-redis"] = {"has_health": False}
        base = obs["docker"]["db"]
        obs["docker"]["zzz"] = dict(base)
        exp["containers"]["zzz"] = {"has_health": False}
        for n in ("zzz", "common-postgres", "common-redis"):
            obs["docker"][n] = dict(base, running=False, status="exited")
        obs["docker"]["old"] = dict(base, running=False, status="exited")  # stopped on purpose
        obs["user_units"]["hermes-gateway.service"]["ActiveState"] = "inactive"
        plan = nbc.plan_heal(exp, obs)
        self.assertEqual(plan["containers"][:2], ["common-postgres", "common-redis"])
        self.assertIn("zzz", plan["containers"])
        self.assertNotIn("old", plan["containers"])
        self.assertEqual(plan["user_units"], ["hermes-gateway.service"])

    def test_no_user_unit_action_without_a_user_manager(self):
        exp, obs = healthy()
        obs["user_manager"] = False
        obs["user_units"]["hermes-gateway.service"]["ActiveState"] = "inactive"
        self.assertEqual(nbc.plan_heal(exp, obs)["user_units"], [])

    def test_dry_run_does_nothing_but_reports(self):
        plan = {"user_units": ["hermes-gateway.service"], "containers": ["x"]}
        done = nbc.do_heal(plan, dry=True)
        self.assertEqual(len(done), 2)


class Discord(unittest.TestCase):
    """The post-boot message is the whole point: prove how the request is built, offline."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        nbc.WEBHOOK_ENV = self.env

    def tearDown(self):
        self.tmp.cleanup()

    def test_webhook_is_read_without_quotes_and_missing_file_means_no_url(self):
        self.assertEqual(nbc.webhook_url(), "")
        self.env.write_text("OTHER=1\nDISCORD_WEBHOOK_URL='https://example.invalid/hook/abc'\n")
        self.assertEqual(nbc.webhook_url(), "https://example.invalid/hook/abc")

    def test_request_shape_user_agent_no_mentions_and_length_cap(self):
        from unittest import mock
        import json as _json
        self.env.write_text("DISCORD_WEBHOOK_URL=https://example.invalid/hook/abc\n")
        seen = {}

        class FakeResp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakeOpener:
            def open(self, req, timeout=None):
                seen["url"], seen["body"], seen["headers"], seen["timeout"] = (
                    req.full_url, _json.loads(req.data), dict(req.header_items()), timeout)
                return FakeResp()

        with mock.patch.object(nbc.urllib.request, "build_opener", return_value=FakeOpener()):
            self.assertTrue(nbc.send_discord("x" * 5000))
        self.assertEqual(seen["url"], "https://example.invalid/hook/abc")
        self.assertLessEqual(len(seen["body"]["content"]), 1990)
        self.assertEqual(seen["body"]["allowed_mentions"], {"parse": []})
        self.assertEqual(seen["headers"]["User-agent"], "nas-boot-check/1.0")  # Discord refuses the default urllib UA
        self.assertEqual(seen["headers"]["Content-type"], "application/json")

    def test_failure_modes_return_false_and_never_raise(self):
        from unittest import mock
        self.assertFalse(nbc.send_discord("hi"))  # no webhook configured
        self.env.write_text("DISCORD_WEBHOOK_URL=http://not-https.invalid/x\n")
        self.assertFalse(nbc.send_discord("hi"))  # refuses plain http: a webhook URL is a secret
        self.env.write_text("DISCORD_WEBHOOK_URL=https://example.invalid/hook/abc\n")

        class Boom:
            def open(self, *a, **k): raise OSError("network down")

        with mock.patch.object(nbc.urllib.request, "build_opener", return_value=Boom()):
            self.assertFalse(nbc.send_discord("hi"))


class Report(unittest.TestCase):
    def test_discord_message_for_a_clean_boot_fits_and_is_green(self):
        exp, obs = healthy()
        msg = nbc.render_discord(nbc.evaluate(exp, obs), 192, 192, [])
        self.assertTrue(msg.startswith("✅ **NAS redémarré : tout est revenu**"), msg)
        self.assertLess(len(msg), 1990)

    def test_discord_message_for_failures_lists_them_and_the_recovery_hint(self):
        exp, obs = healthy()
        obs["docker"]["db"].update(running=False, status="exited", exit=137)
        msg = nbc.render_discord(nbc.evaluate(exp, obs), 900, 900, ["conteneur démarré : db"])
        self.assertTrue(msg.startswith("❌"), msg)
        self.assertIn("db : arrêté", msg)
        self.assertIn("🔧", msg)
        self.assertIn("nas-boot-check run", msg)

    def test_update_info_line_says_what_it_is(self):
        exp, obs = healthy()
        obs["versions"] = {"kernel": "6.19.2", "docker": "29.6.2"}
        msg = nbc.render_discord(nbc.evaluate(exp, obs), 120, 120, [])
        self.assertIn("ℹ️ mise à jour détectée : kernel 6.18.15 → 6.19.2", msg)

    def test_manual_check_is_not_called_a_reboot(self):
        exp, obs = healthy()
        msg = nbc.render_discord(nbc.evaluate(exp, obs), 5 * 86400, 30, [])
        self.assertIn("Contrôle manuel", msg)
        self.assertNotIn("NAS redémarré", msg)


if __name__ == "__main__":
    unittest.main()
