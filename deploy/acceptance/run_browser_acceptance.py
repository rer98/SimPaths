#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Exercise the real VM frontend with an isolated catalogue, Redis and browser.

Requires Docker access, the prepared image and deploy/acceptance/requirements.txt installed
alongside the frontend's requirements-vm.txt. No production sources are edited.

@author ross richardson
"""
import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import traceback

import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']
workflow = load_tool('_workflow.py')


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def assert_private_network(container, docker_client):
    """Check real Docker topology and host connectivity, independent of frontend mocks."""
    import hashlib
    import ipaddress
    import httpx
    container.reload()
    attrs = container.attrs
    assert not attrs.get('HostConfig', {}).get('PortBindings'), 'Model publishes a host port'
    settings = attrs['NetworkSettings']
    assert not any((settings.get('Ports') or {}).values()), 'Model has published ports'
    labels = attrs['Config']['Labels']
    sid, model = labels['jasmine.session_id'], labels['jasmine.model_id']
    import uuid
    owner = str(uuid.UUID(labels['jasmine.deployment_id']))
    name = 'jms-' + hashlib.sha256(sid.encode()).hexdigest()[:10]
    assert set(settings['Networks']) == {name}, 'Unexpected network attachment'
    network = docker_client.networks.get(name)
    assert network.attrs['Internal'] is True
    assert network.attrs['Driver'] == 'bridge'
    assert network.attrs['Labels']['jasmine.session_id'] == sid
    assert network.attrs['Labels']['jasmine.model_id'] == model
    assert network.attrs['Labels']['jasmine.deployment_id'] == owner
    attachment = settings['Networks'][name]
    assert attachment['NetworkID'] == network.id
    address = ipaddress.IPv4Address(attachment['IPAddress'])
    assert any(address in ipaddress.IPv4Network(cidr)
               for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    assert any(address in ipaddress.ip_network(c['Subnet'])
               for c in network.attrs['IPAM']['Config'])
    url = f'http://{address}:7070'
    assert httpx.get(url + '/health', timeout=10, trust_env=False).status_code == 200
    assert httpx.get(url + '/simulation/status', timeout=10, trust_env=False).status_code == 401
    return {'java_private_endpoint': url, 'network_id': network.id, 'published_ports': False, 'deployment_id': owner}


def chart_errors(console):
    """Chart adapters log Java failures without raising browser exceptions."""
    return sorted({line.strip() for line in console.splitlines()
                   if "Processor: error processing" in line})


def remove_test_networks(docker_client, model_id):
    """Remove only this invocation's empty session networks after its containers."""
    import hashlib
    for network in docker_client.networks.list(filters={'label': f'jasmine.model_id={model_id}'}):
        labels = network.attrs.get('Labels') or {}
        sid = labels.get('jasmine.session_id')
        if not isinstance(sid, str) or not sid or labels.get('jasmine.model_id') != model_id:
            continue
        if network.name == 'jms-' + hashlib.sha256(sid.encode()).hexdigest()[:10]:
            network.remove()


def verify_deployment_settings(population, attrs, logs, storage):
    """Verify Docker allocation and the simulation JVM's own startup diagnostic."""
    heap_gib = 2 if population == 20000 else 3
    memory_gib = 4 if population == 20000 else 5
    assert attrs["HostConfig"]["Memory"] == memory_gib * 1024**3
    assert attrs["HostConfig"]["NanoCpus"] == 2_000_000_000
    settings = dict(e.split("=", 1) for e in attrs["Config"]["Env"] if "=" in e)
    assert settings["JAVA_OPTS"] == f"-Xmx{heap_gib}g", settings.get("JAVA_OPTS")
    match = re.search(r"Max\. Heap Size(?: \(Estimated\))?:\s*([0-9.]+)([KMGT])", logs)
    assert match, "JVM startup heap diagnostic missing; image must use -XshowSettings:vm"
    heap_bytes = float(match[1]) * 1024 ** ("KMGT".index(match[2]) + 1)
    assert abs(heap_bytes - heap_gib*1024**3) <= 0.01*1024**3, match.group()
    assert storage.get("enabled") and storage.get("cleanupEnabled"), storage
    assert storage["allowanceBytes"] == 10*1024**3, storage
    assert settings["JASMINE_STORAGE_WARN_BYTES"] == str(3*1024**3)
    assert settings["JASMINE_STORAGE_RESERVE_BYTES"] == str(2*1024**3)
    return dict(heap_bytes=heap_bytes, container_bytes=memory_gib*1024**3,
                cpus=2, storage_allowance_bytes=10*1024**3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-url", help="Existing isolated test frontend; no frontend/Redis is started")
    parser.add_argument("--model-id", help="Unique test model ID required with --external-url")
    parser.add_argument("--frontend", type=Path, default=workflow.frontend_path())
    parser.add_argument("--deployment-profile", action="store_true", help="Use and verify the recommended heap/container/CPU/storage configuration")
    parser.add_argument("--storage-check", action="store_true", help="Test storage warnings, build admission and confirmed cleanup on the new runtime")
    parser.add_argument("--detailed-access", action="store_true", help="Verify enabled database exploration and file exports")
    parser.add_argument("--population", type=int, choices=(20000, 50000), default=50000)
    parser.add_argument("--image", help="Image to test; defaults to the selected population in the catalogue")
    parser.add_argument("--output", type=Path, help="New directory; defaults to a dated directory under ~/simpaths-browser-tests")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--step-only", action="store_true", help="Validate scheduled chart updates and reset/rebuild without continuous Start/Pause")
    parser.add_argument("--catalogue", type=Path, help="Catalogue for default image selection")
    args = parser.parse_args()
    args.frontend = args.frontend.expanduser().resolve()
    args.image = workflow.quickstart_image(args.frontend, args.population, args.image, args.catalogue)
    if bool(args.external_url) != bool(args.model_id):
        parser.error("--external-url and --model-id must be supplied together")
    if args.external_url and not args.model_id.startswith("simpaths-acceptance-"):
        parser.error("External mode requires a dedicated simpaths-acceptance- model ID")
    if args.storage_check and not args.detailed_access:
        parser.error("--storage-check requires --detailed-access")
    import docker
    import httpx
    from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeoutError

    out = (args.output or Path.home()/"simpaths-browser-tests"/dt.datetime.now().strftime("run-%Y%m%d-%H%M%S")).resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.chmod(out, 0o700)
    report = {"status": "running", "checks": [], "page_errors": [], "console_errors": [], "failed_requests": [], "output": str(out)}
    report["step_only"] = args.step_only
    client = docker.from_env()
    redis_container = process = None
    model_id = args.model_id or "simpaths-acceptance-" + secrets.token_hex(6)
    report["model_id"] = model_id
    frontend_log = (out/"frontend.log").open("w")

    def passed(name, **detail):
        report["checks"].append({"name": name, "status": "passed", **detail})
        print("PASS:", name, flush=True)

    try:
        report["image_id"] = client.images.get(args.image).id
        frontend = args.frontend.resolve()
        if args.external_url:
            base = args.external_url.rstrip("/")
            report["external_frontend"] = base
        else:
            work = out/"frontend"
            # Copy tracked files only, with current working-tree contents. Never copy
            # .env, credentials, user databases or another instance's configuration.
            tracked = subprocess.check_output(["git", "-C", str(frontend), "ls-files", "-z"]).decode().split("\0")
            # Do not silently exercise stale code when a new security module has
            # not yet been staged. The existing tracked-file copy stays explicit.
            required = ['jasmine_web/public_fetch.py', 'jasmine_web/request_limits.py',
                        'jasmine_web/session_security.py', 'static/jasmine_workbooks.js',
                        'static/jasmine_workbook_worker.js']
            if any(name not in tracked for name in required):
                raise RuntimeError('Stage the new frontend security files with git add before isolated acceptance; it copies tracked files only.')
            if args.storage_check:
                tracked += ["jasmine_web/storage_policy.py", "static/jasmine_storage.js"]
            if args.deployment_profile:
                tracked += ["jasmine_web/heap_policy.py"]
            for name in tracked:
                source = frontend/name
                if not name or source.is_symlink() or not source.is_file():
                    continue
                if not (source.suffix == ".py" or name.startswith("static/")):
                    continue
                target = work/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            (work/"models.json").write_text(json.dumps({"models": [{
                "id": model_id, "name": f"SimPaths UK Quick Start - {args.population:,} people", "requiresAuth": False,
                "icon": "fa-chart-line", "color": "#26734d", "description": "Isolated UK training-data browser test",
                "deployment": {"image": args.image, "memory": ("4Gi" if args.population == 20000 else "5Gi") if args.deployment_profile else "6Gi", "cpu": 2,
                    **({"java_heap_gib": 2 if args.population == 20000 else 3} if args.deployment_profile else {}),
                    **({"session_storage": {"allowance_gib": 10, "cleanup_enabled": True}} if args.storage_check or args.deployment_profile else {})}
            }]}))
            report["frontend_revision"] = subprocess.check_output(["git", "-C", str(frontend), "rev-parse", "HEAD"], text=True).strip()
            report["frontend_worktree_status"] = subprocess.check_output(["git", "-C", str(frontend), "status", "--short"], text=True)
            (out/"python-packages.txt").write_text(subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True))
            redis_port, web_port = free_port(), free_port()
            redis_container = client.containers.run("redis:alpine", detach=True,
                ports={"6379/tcp": ("127.0.0.1", redis_port)},
                command=["redis-server", "--save", "", "--appendonly", "no"],
                labels={"simpaths.acceptance": model_id})
            env = {k: v for k, v in os.environ.items() if not k.startswith(("REDIS_", "JASMINE_", "VM_"))}
            env.update(DEPLOY_MODE="vm", VM_SECURITY_MODE="development", COOKIE_SECURE="false", REDIS_HOST="127.0.0.1", REDIS_PORT=str(redis_port),
                REDIS_DB="0", ADMIN_PASSWORD=secrets.token_urlsafe(32), SESSION_SECRET=secrets.token_urlsafe(32),
                HARD_RESET_SECRET=secrets.token_urlsafe(32), PYTHONUNBUFFERED="1", PYTHON_DOTENV_DISABLED="1")
            # The isolated Redis receives its own persistent deployment identity.
            # Exercise ordinary ownership-scoped cleanup as well as provisioning.
            (work/"acceptance_server.py").write_text("# (C) Copyright 2026, by Ross Richardson\n# Isolated browser-test server with deployment-scoped cleanup.\n# @author ross richardson\nimport app\nimport uvicorn\nuvicorn.run(app.app, host='127.0.0.1', port=PORT)\n".replace("port=PORT", f"port={web_port}"))
            process = subprocess.Popen([sys.executable, "acceptance_server.py"], cwd=work, env=env,
                stdout=frontend_log, stderr=subprocess.STDOUT)
            base = f"http://127.0.0.1:{web_port}"
            deadline = time.monotonic()+60
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Frontend exited; see frontend.log")
                try:
                    if httpx.get(base+"/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Frontend did not become healthy")
                time.sleep(.5)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, extra_http_headers={"Origin": base})
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page.set_default_timeout(30000)
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("console", lambda msg: report["console_errors"].append(msg.text) if msg.type == "error" else None)
            page.on("requestfailed", lambda req: report["failed_requests"].append({"url": req.url, "failure": req.failure}))
            def dismiss_dialog(dialog):
                dialog.dismiss()
            page.on("dialog", dismiss_dialog)
            try:
                page.goto(base)
                page.screenshot(path=str(out/"01-catalogue.png"), full_page=True)
                page.locator(".model-card").click()
                page.wait_for_url("**/sim/**", timeout=180000)
                expect(page.locator("#param-form")).to_be_visible(timeout=180000)
                passed("launch reaches parameters without startup questions")
                session_containers = client.containers.list(filters={"label": f"jasmine.model_id={model_id}"})
                assert len(session_containers) == 1
                passed("private network connectivity and backend authentication",
                       **assert_private_network(session_containers[0], client))
                page.locator("#readme-btn").click()
                readme = page.locator(".readme-content")
                expect(readme).to_be_visible()
                text = readme.inner_text()
                assert "training" in text.lower() and "prepared" in text.lower(), text
                (out/"readme.txt").write_text(text)
                page.screenshot(path=str(out/"02-readme.png"), full_page=True)
                page.locator("#readme-close-btn").click()
                passed("README describes training data and prepared population")
                params = page.locator("#param-form").evaluate("form => Object.fromEntries(new FormData(form))")
                report["parameters"] = params
                assert "UK" in text, "README does not identify UK profile"
                for key, value in {"popSize": str(args.population), "endYear": "2026", "startYear": "2019", "randomSeedIfFixed": "606"}.items():
                    assert str(params.get(key)) == value, (key, params.get(key))
                passed("fixed Quick Start defaults")
                # Reject locally first, then bypass the form to exercise server admission.
                population = page.locator('[name="popSize"]')
                if population.is_enabled():
                    old_url = page.url
                    population.fill(str(args.population-1))
                    page.locator("#toolbar-build-btn").click()
                    expect(page.locator("#console")).to_contain_text("Build cancelled: please correct invalid parameter values.")
                    expect(population.locator("..")).to_contain_text(f"Prepared Quick Start requires popSize={args.population}")
                    expect(page.locator("#toolbar-build-btn")).to_be_enabled()
                    expect(population).to_have_value(str(args.population-1))
                    assert page.url == old_url
                    passed("sidebar blocks invalid population without Reset")

                    submitted = page.evaluate("parseFormParams(document.getElementById('param-form'))")
                    sid = page.url.rstrip("/").split("/")[-1]
                    response = page.request.post(base + "/build-json/" + sid, data=submitted)
                    assert response.ok, response.text()
                    page.wait_for_function("""async sid => {
                        const r = await fetch('/status/' + sid);
                        const data = await r.json();
                        return data.status === 'parameters_rejected';
                    }""", arg=sid, timeout=30000)
                    expect(page.locator("#status")).to_contain_text("Build not started:", timeout=30000)
                    expect(page.locator("#status")).to_contain_text(f"Prepared Quick Start requires popSize={args.population}")
                    expect(page.locator("#toolbar-build-btn")).to_be_enabled()
                    expect(page.locator("#offline-banner")).not_to_have_class(re.compile(r".*is-visible.*"))
                    expect(population).to_have_value(str(args.population-1))
                    assert page.url == old_url
                    page.screenshot(path=str(out/"03-profile-rejection.png"), full_page=True)
                    population.fill(str(args.population))
                    passed("server rejects invalid population before Build; correction retains session", recovery="retry without Reset")
                else:
                    report["checks"].append({"name": "conflicting profile", "status": "not_exercised", "reason": "UI field disabled"})
                if args.deployment_profile:
                    container = client.containers.list(filters={"label": f"jasmine.model_id={model_id}"})[0]
                    container.reload()
                    sid = page.url.rstrip("/").split("/")[-1]
                    storage = page.request.get(base + "/storage/" + sid).json()
                    verified = verify_deployment_settings(args.population, container.attrs,
                        container.logs().decode(errors="replace"), storage)
                    passed("deployment heap, container, CPUs and storage policy verified", **verified)
                for build in (1, 2):
                    started = time.monotonic()
                    page.locator("#toolbar-build-btn").click()
                    expect(page.locator("#step-btn")).to_be_enabled(timeout=600000)
                    expect(page.locator(".js-plotly-plot").first).to_be_visible(timeout=60000)
                    page.wait_for_function("n => (document.querySelector('#console').textContent.match(/Found processed dataset - preparing for simulation/g) || []).length >= n", arg=build)
                    passed(f"build {build} ready with charts", seconds=round(time.monotonic()-started, 2), charts=page.locator(".js-plotly-plot").count())
                    # Collector events precede the observer. Step until its first
                    # scheduled update rather than assuming a fixed event count.
                    histogram_titles = ("Individual Gross Earnings (yearly)",
                                        "Equivalised Disposable Income of Benefit Unit (yearly)")
                    page.wait_for_function("() => chartDivs.length > 0 && chartDivs.every(c => document.getElementById(c.id)?.classList.contains('js-plotly-plot'))", timeout=60000)
                    rendered_ids = page.evaluate("() => chartDivs.map(c => c.id)")
                    assert len(rendered_ids) == len(set(rendered_ids)), "Duplicate chart DOM IDs"
                    server_ids = page.evaluate("async () => (await (await fetch('/charts/' + CONFIG.SESSION_ID)).json()).charts.map(c => c.id)")
                    assert sorted(server_ids) == sorted(rendered_ids), "Some server charts were not rendered"
                    histogram_ids = page.evaluate("titles => titles.map(t => chartDivs.find(c => c.title === t)?.id)", histogram_titles)
                    assert all(histogram_ids), "Missing histogram"
                    for steps in range(1, 21):
                        with page.expect_response(lambda r: "/step/" in r.url or "/step-sim/" in r.url, timeout=120000) as response:
                            page.locator("#step-btn").click()
                        assert response.value.ok, response.value.status
                        try:
                            page.wait_for_function("ids => ids.every(id => { const e = document.getElementById(id); return e?.data?.some(t => t.y?.some(v => Number.isFinite(v) && v > 0)); })", arg=histogram_ids, timeout=3000)
                            break
                        except PlaywrightTimeoutError:
                            if steps == 20:
                                raise
                    passed(f"step after build {build}", steps=steps, response=response.value.json())
                    # Assert the plotted data, not merely the existence of frames.
                    for title in ("Individual Gross Earnings (yearly)",
                                  "Equivalised Disposable Income of Benefit Unit (yearly)"):
                        chart_id = histogram_ids[histogram_titles.index(title)]
                        page.wait_for_function("id => { const e = document.getElementById(id); return e?.data?.length && e.data.every(t => t.name && t.name !== 'Unknown' && t.y?.length && t.y.every(Number.isFinite) && t.y.some(v => v > 0)); }", arg=chart_id, timeout=60000)
                        evidence = page.locator("#" + chart_id).evaluate("e => ({title: e.layout.title, traces: e.data.map(t => ({name: t.name, bins: t.y.length, totalWeight: t.y.reduce((a,b) => a+b, 0)}))})")
                        page.locator("#" + chart_id).screenshot(path=str(out/f"histogram-{build}-{chart_id}.png"))
                        passed(f"build {build}: populated histogram {title}", **evidence)
                    page.screenshot(path=str(out/f"04-build-{build}.png"), full_page=True)
                    if build == 1 and not args.step_only:
                        page.locator("#start-btn").click()
                        expect(page.locator("#pause-btn")).to_be_enabled(timeout=120000)
                        # Allow scheduled observer events to supply time-series
                        # data, rather than pausing immediately after Start.
                        page.wait_for_function("() => [...document.querySelectorAll('.js-plotly-plot')].some(e => e.data?.some(t => t.type === 'scatter' && t.x?.some(x => Number.isFinite(x) && x >= 2019) && t.y?.some(Number.isFinite)))", timeout=180000)
                        passed("simulation supplies plotted time-series data")
                        # Exercise the long annual event rather than pausing between short initial events.
                        expect(page.locator("#console")).to_contain_text("Starting year 2020", timeout=180000)
                        pause_started = time.monotonic()
                        with page.expect_response(lambda r: "/pause/" in r.url, timeout=330000) as pause_response:
                            page.locator("#pause-btn").click()
                            expect(page.locator("#status")).to_contain_text("Pausing after the current simulation step")
                            expect(page.locator("#reset-btn")).to_be_disabled()
                            expect(page.locator("#start-btn")).to_be_disabled()
                        assert pause_response.value.ok and pause_response.value.json().get("status") == "paused", pause_response.value.text()
                        expect(page.locator("#start-btn")).to_be_enabled(timeout=120000)
                        passed("start/pause controls recover", pause_seconds=round(time.monotonic()-pause_started, 2), response=pause_response.value.json())
                        # Record this chart after the next observer update, rather than
                        # mistaking a pause before that event for missing annual data.
                        income_title = "Disp income by Gender And Education"
                        income = None
                        for observer_step in range(21):
                            income = page.evaluate("""async title => {
                                const payload = await (await fetch('/charts/' + CONFIG.SESSION_ID)).json();
                                return payload.charts.find(c => c.title === title);
                            }""", income_title)
                            if income and any(any(isinstance(x, (int, float)) and x >= 2020 for x in series.get("x", [])) for series in income.get("series", [])):
                                break
                            if observer_step == 20:
                                break
                            with page.expect_response(lambda r: "/step/" in r.url or "/step-sim/" in r.url, timeout=120000):
                                page.locator("#step-btn").click()
                        page.wait_for_function("() => !chartRequestPending", timeout=60000)
                        page.evaluate("async () => await updateChartsOnce()")
                        rendered_income = page.evaluate("""title => {
                            const chart = chartDivs.find(c => c.title === title);
                            const element = chart && document.getElementById(chart.id);
                            return element ? {id: chart.id, series: element.data.map(t => ({name: t.name, x: t.x, y: t.y}))} : null;
                        }""", income_title)
                        (out / "income-chart-diagnostic.json").write_text(json.dumps({"server": income, "browser": rendered_income}, indent=2))
                    if build == 1:
                        with page.expect_response(lambda r: "/reset/" in r.url, timeout=330000) as response:
                            page.locator("#reset-btn").click()
                        assert response.value.ok and not response.value.json().get("error"), response.value.text()
                        expect(page.locator("#toolbar-build-btn")).to_be_enabled()
                        passed("ordinary reset")
                report["final_status"] = page.locator("#status").inner_text()
                if args.detailed_access:
                    expect(page.locator("#db-explorer-btn")).to_be_enabled()
                    expect(page.locator("#export-btn")).to_be_enabled()
                    page.locator("#db-explorer-btn").click()
                    expect(page.locator("#db-tables")).to_contain_text("PROCESSED", timeout=60000)
                    page.locator("#db-sql").fill("SELECT COUNT(*) AS N FROM PROCESSED")
                    page.locator("#db-run-btn").click()
                    expect(page.locator("#db-results .db-results-table tbody td")).to_have_text("1", timeout=60000)
                    passed("input database query returns prepared population record")
                    rejected_sql = page.request.post(base + "/java/" + sid + "/simulation/db/query",
                        data={"role": "input", "sql": "SELECT FILE_READ('input/system_bu_names.xlsx')"})
                    assert rejected_sql.status == 400, rejected_sql.text()
                    expect(page.locator("#step-btn")).to_be_enabled()
                    passed("unsafe SQL rejected without changing simulation lifecycle")
                    page.locator('input[name="db-role"][value="output"]').check()
                    expect(page.locator("#db-timestamp option")).to_have_count(1, timeout=60000)
                    page.locator("#db-sql").fill("SELECT COUNT(*) AS N FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='PUBLIC'")
                    with page.expect_response(lambda r: "/simulation/db/query" in r.url and r.request.method == "POST" and "INFORMATION_SCHEMA.TABLES" in (r.request.post_data or ""), timeout=60000) as query_response:
                        page.locator("#db-run-btn").click()
                    query_result = query_response.value.json()
                    assert not query_result.get("error") and query_result["rows"][0][0] > 0, query_result
                    page.wait_for_function("() => Number(document.querySelector('#db-results .db-results-table tbody td')?.textContent) > 0", timeout=60000)
                    passed("shared output database is browsable after reset/rebuild")
                    page.locator("#db-close-btn").click()
                    page.locator("#export-btn").click()
                    expect(page.locator("#export-modal details")).to_have_count(2, timeout=60000)
                    first_run = page.locator("#export-modal details").first
                    first_run.locator("summary").click()
                    download_button = first_run.locator('button.export-file-download[data-path$="HealthStatistics.csv"]').first
                    with page.expect_download(timeout=60000) as download_info:
                        download_button.click()
                    download = download_info.value
                    assert download.failure() is None, download.failure()
                    downloaded = out/"downloaded-HealthStatistics.csv"
                    download.save_as(downloaded)
                    assert downloaded.stat().st_size > 0
                    passed("run file downloads through browser", filename=download.suggested_filename, bytes=downloaded.stat().st_size)
                    page.locator("#export-close-btn").click()
                else:
                    expect(page.locator("#db-explorer-btn")).to_be_disabled()
                    expect(page.locator("#export-btn")).to_be_enabled()
                    passed("detailed database and output access remain disabled")
                if args.storage_check:
                    sid = page.url.rstrip("/").split("/")[-1]
                    active_preview = page.request.post(base + "/storage-preview/" + sid).json()
                    assert "Reset" in active_preview.get("error", ""), active_preview
                    with page.expect_response(lambda r: "/reset/" in r.url and r.request.method == "POST", timeout=330000) as reset_response:
                        page.locator("#reset-btn").click()
                    assert not reset_response.value.json().get("error")
                    container = client.containers.list(filters={"label": f"jasmine.model_id={model_id}"})[0]
                    def command(command):
                        result = container.exec_run(command)
                        assert result.exit_code == 0, result.output.decode()
                        return result.output.decode()
                    # Use the runtime-writable output directory; /app itself is protected.
                    # Sparse, test-owned file changes logical usage without filling the laptop disk.
                    command(["truncate", "-s", "10G", "/app/output/storage-acceptance-padding"])
                    storage_status = page.request.get(base + "/storage/" + sid).json()
                    assert storage_status["buildBlocked"] and storage_status["warning"], storage_status
                    original_url = page.url
                    page.locator("#toolbar-build-btn").click()
                    expect(page.locator("#status")).to_contain_text("insufficient session storage", timeout=45000)
                    expect(page.locator("#toolbar-build-btn")).to_be_enabled()
                    assert page.url == original_url
                    command(["rm", "/app/output/storage-acceptance-padding"])
                    passed("storage admission rejects before build and keeps session recoverable")
                    preview = page.request.post(base + "/storage-preview/" + sid).json()
                    assert len(preview["runs"]) == 2, preview
                    first, second = sorted(preview["runs"], key=lambda r: r["path"])
                    protected_paths = [item["path"] for run in preview["runs"] for item in run["retained"]
                                       if item["path"].endswith(".mv.db")]
                    assert any("/database/out.mv.db" in p for p in protected_paths), preview
                    assert first["path"] + "/input/input.mv.db" in protected_paths, preview
                    assert second["path"] + "/input/input.mv.db" not in protected_paths, preview
                    def hashes():
                        return {p: command(["sha256sum", "/app/" + p]).split()[0] for p in protected_paths}
                    protected_before = hashes()
                    def output_query(sql):
                        result = page.request.post(base + "/java/" + sid + "/simulation/db/query",
                            data={"role": "output", "timestamp": first["path"].split("/")[-1], "sql": sql}).json()
                        assert not result.get("error"), result
                        return result["rows"]
                    experiments_before = output_query("SELECT ID FROM JASMINE_EXPERIMENT ORDER BY ID")
                    assert len(experiments_before) == 2, experiments_before
                    page.locator("#export-btn").click()
                    expect(page.locator("#storage-cleanup-btn")).to_be_visible(timeout=45000)
                    page.locator("#storage-cleanup-btn").click()
                    dialog = page.locator("dialog").filter(has_text="Review storage cleanup")
                    expect(dialog).to_be_visible()
                    expect(dialog.locator('input[type="checkbox"]')).to_have_count(2)
                    expect(dialog).to_contain_text("Saved processed population")
                    expect(dialog).to_contain_text("Shared output database")
                    for checkbox in dialog.locator('input[type="checkbox"]').all(): checkbox.check()
                    page.remove_listener("dialog", dismiss_dialog)
                    page.once("dialog", lambda confirmation: confirmation.accept())
                    with page.expect_response(lambda r: "/storage-delete/" in r.url and r.request.method == "POST") as deletion:
                        dialog.get_by_role("button", name="Clear selected runs").click()
                    result = deletion.value.json()
                    assert result.get("complete"), result
                    assert second["path"] in result["removedRuns"], result
                    assert first["path"] + "/input/options.txt" in result["deleted"], result
                    assert any(p.startswith(first["path"] + "/input/") and p.endswith(".xlsx") for p in result["deleted"]), result
                    expect(dialog).to_contain_text("Deleted", timeout=60000)
                    page.on("dialog", dismiss_dialog)
                    dialog.get_by_role("button", name="Close", exact=True).click()
                    assert protected_before == hashes()
                    command(["test", "!", "-e", "/app/" + second["path"]])
                    assert output_query("SELECT ID FROM JASMINE_EXPERIMENT ORDER BY ID") == experiments_before
                    passed("run-level cleanup deletes unused input DB and removes its run; protected DB hashes and both experiment records survive",
                           deleted_files=len(result["deleted"]), reclaimed_bytes=result["bytes"], removed_runs=result["removedRuns"])
                    started = time.monotonic()
                    page.locator("#toolbar-build-btn").click()
                    expect(page.locator("#step-btn")).to_be_enabled(timeout=600000)
                    page.wait_for_function("() => (document.querySelector('#console').textContent.match(/Found processed dataset - preparing for simulation/g) || []).length >= 3")
                    passed("build after cleanup reuses prepared population in same session", seconds=round(time.monotonic()-started, 2))
                    assert len(output_query("SELECT ID FROM JASMINE_EXPERIMENT ORDER BY ID")) == 3
                    passed("shared output database retains earlier experiments and records the new build")
                console = page.locator("#console").inner_text()
                (out/"console.txt").write_text(console)
                page.screenshot(path=str(out/"05-final.png"), full_page=True)
                assert not report["page_errors"], report["page_errors"]
                passed("no uncaught browser exceptions")
                report["chart_errors"] = chart_errors(console)
                assert not report["chart_errors"], report["chart_errors"]
                passed("no Java chart-processing errors")
                report["status"] = "passed"
            finally:
                try:
                    page.screenshot(path=str(out/"last-page.png"), full_page=True)
                    (out/"last-page.txt").write_text(page.locator("body").inner_text())
                finally:
                    context.tracing.stop(path=str(out/"trace.zip"))
                    browser.close()
    except (Exception, KeyboardInterrupt):
        report["status"] = "failed"
        report["error"] = traceback.format_exc()
        print(report["error"], file=sys.stderr)
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        # Unique per-invocation model label ensures cleanup cannot remove another
        # deployment's containers, even when tests fail before obtaining a session.
        try:
            for container in client.containers.list(all=True, filters={"label": f"jasmine.model_id={model_id}"}):
                (out/f"model-{container.short_id}.log").write_bytes(container.logs())
                container.remove(force=True)
            remove_test_networks(client, model_id)
            if redis_container is not None:
                redis_container.remove(force=True)
        except Exception as error:
            report["cleanup_error"] = str(error)
            report["status"] = "failed"
        frontend_log.close()
        (out/"report.json").write_text(json.dumps(report, indent=2))
        print(f"{report['status'].upper()}: {out}/report.json", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
