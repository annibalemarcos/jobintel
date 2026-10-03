#!/usr/bin/env python3
"""Capture privacy-conscious, read-only screenshots of the local JobIntel dashboard."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import BrowserContext, Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8765/"
OUTPUT_DIR = ROOT / "docs" / "screenshots"
TIMEOUT_MS = 12_000

MAIN_SHOTS = [
    ("overview", "01-overview.png", "Visão geral"),
    ("companies", "02-companies.png", "Empresas"),
    ("jobs", "03-jobs.png", "Vagas"),
    ("applications", "04-applications.png", "Candidaturas"),
    ("emails", "05-contacts.png", "Contatos"),
    ("careers", "06-careers.png", "Carreiras"),
    ("runs", "07-runs.png", "Execuções"),
    ("profile", "08-profile.png", "Perfil"),
]


def local_state_snapshot() -> dict[str, str]:
    """Fingerprint local SQLite artifacts so accidental UI writes are visible."""
    data_dir = ROOT / ".dashboard_data"
    snapshot: dict[str, str] = {}
    if not data_dir.is_dir():
        return snapshot
    for path in sorted(data_dir.glob("*.sqlite3*")):
        if path.is_file():
            snapshot[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def ensure_dashboard() -> None:
    try:
        request = urllib.request.Request(BASE_URL, method="GET")
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status != 200:
                raise OSError(f"HTTP {response.status}")
    except (OSError, urllib.error.URLError) as error:
        raise SystemExit(
            "JobIntel dashboard is not running. Start it with .\\dashboard.bat"
        ) from error


def request_guard(route) -> None:
    request = route.request
    parsed = urlsplit(request.url)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 8765:
        route.abort("blockedbyclient")
        return
    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        route.continue_()
        return
    # This endpoint computes a preview from persisted data, without generating or saving materials.
    if method == "POST" and parsed.path == "/api/application-materials/preview":
        try:
            body = json.loads(request.post_data or "{}")
        except (TypeError, ValueError):
            route.abort("blockedbyclient")
            return
        if isinstance(body, dict) and body.get("regenerate", False) is False:
            route.continue_()
            return
    route.abort("blockedbyclient")


def wait_for_dashboard(page: Page) -> None:
    page.locator("#result-count").wait_for(state="visible", timeout=TIMEOUT_MS)
    page.wait_for_function(
        "() => !document.querySelector('#result-count')?.textContent?.includes('Carregando')",
        timeout=TIMEOUT_MS,
    )
    page.wait_for_timeout(350)


def sanitize(page: Page, *, profile: bool = False, modal: str | None = None) -> None:
    """Redact personal content only in this browser DOM, never in stored data."""
    page.evaluate(
        r"""({profile, modal}) => {
          const personalEmail = /[A-Z0-9._%+-]+@(?:gmail|googlemail|outlook|hotmail|live|yahoo|icloud|protonmail|proton)\.[A-Z]{2,}/ig;
          const secret = /\b(?:sk-[A-Za-z0-9_-]{16,}|(?:api[_ -]?key|token|password)\s*[:=]\s*[^\s,;]+)/ig;
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          const nodes=[]; while(walker.nextNode()) nodes.push(walker.currentNode);
          for (const node of nodes) {
            if (!node.parentElement || /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA|INPUT)$/i.test(node.parentElement.tagName)) continue;
            let value=node.nodeValue.replace(personalEmail, '[e-mail pessoal ocultado]')
              .replace(secret, '[segredo ocultado]');
            if(value.includes('/Users/') || value.includes('/home/') || value.includes(':\\Users\\')) value='[caminho local ocultado]';
            node.nodeValue=value;
          }
          for (const link of document.querySelectorAll('a[href*="/api/profile/resume"]')) {
            link.textContent='Currículo associado'; link.removeAttribute('href');
          }
          if (profile) {
            const root=document.querySelector('#profile-page');
            if(root){
              root.querySelectorAll('input').forEach(el=>{
                if(['checkbox','radio'].includes(el.type)) el.checked=false;
                else if(!['file','submit','button'].includes(el.type)) el.value='';
              });
              root.querySelectorAll('textarea').forEach(el=>el.value='');
              root.querySelectorAll('select').forEach(el=>el.selectedIndex=0);
              const meta=root.querySelector('#profile-meta-info');
              if(meta) meta.textContent='Perfil profissional · informações pessoais ocultadas';
            }
          }
          if (modal === 'application') {
            document.querySelector('#application-form')?.remove();
            document.querySelector('#application-content #delete-confirmation')?.remove();
            document.querySelector('#application-content .detail-section:last-of-type')?.remove();
            document.querySelectorAll('#application-content button').forEach(el=>el.remove());
          }
          if (modal === 'profile-fit') {
            document.querySelectorAll('#detail-content .detail-section').forEach(section=>{
              const heading=section.querySelector('h3');
              if(heading) {
                for(const child of [...section.children]) if(child!==heading) child.remove();
                const note=document.createElement('p'); note.className='inline-note';
                note.textContent='Conteúdo profissional detalhado ocultado nesta captura.'; section.append(note);
              }
            });
            document.querySelectorAll('#detail-content .detail-meta > div').forEach(item=>{
              if(item.textContent.includes('Perfil analisado')) item.lastChild.textContent=' Perfil ativo';
            });
          }
          if (modal === 'decision') {
            document.querySelector('#user-decision-form')?.remove();
            for(const section of document.querySelectorAll('#detail-content .detail-section')) {
              const heading=section.querySelector('h3')?.textContent||'';
              if(/MINHA DECISÃO|HISTÓRICO/i.test(heading)) section.remove();
              else if(/VALOR DE CARREIRA/i.test(heading)) {
                for(const child of [...section.children]) if(child.tagName!=='H3') child.remove();
                const p=document.createElement('p');p.className='inline-note';
                p.textContent='Detalhe pessoal ocultado nesta captura.';section.append(p);
              }
            }
          }
          if (modal === 'materials') {
            document.querySelectorAll('#materials-output pre').forEach(el=>el.textContent='Conteúdo personalizado ocultado nesta captura.');
            document.querySelectorAll('#materials-output .form-actions').forEach(el=>el.remove());
            document.querySelector('#materials-dialog .materials-controls')?.remove();
            document.querySelector('#materials-dialog #materials-history')?.remove();
            document.querySelector('#materials-dialog .form-actions')?.remove();
          }
          document.querySelectorAll('[title]').forEach(el=>el.removeAttribute('title'));
        }""",
        {"profile": profile, "modal": modal},
    )


def capture(page: Page, filename: str, *, profile: bool = False, modal: str | None = None) -> None:
    sanitize(page, profile=profile, modal=modal)
    page.mouse.move(1400, 980)
    page.wait_for_timeout(450)
    target = OUTPUT_DIR / filename
    page.screenshot(path=str(target), full_page=False, animations="disabled")
    print(f"       saved: {target.relative_to(ROOT).as_posix()}")


def navigate(page: Page, tab: str) -> None:
    page.locator(f'nav button[data-tab="{tab}"]').click()
    if tab == "profile":
        page.locator("#profile-page").wait_for(state="visible", timeout=TIMEOUT_MS)
        page.locator("#profile-form-content").wait_for(state="visible", timeout=TIMEOUT_MS)
    else:
        page.locator("#dashboard-view").wait_for(state="visible", timeout=TIMEOUT_MS)
        page.wait_for_function("() => document.querySelectorAll('#tbody tr').length > 0 || !document.querySelector('#empty')?.hidden", timeout=TIMEOUT_MS)
    page.wait_for_timeout(350)


def optional(page: Page, index: int, label: str, filename: str, action, *, modal: str | None = None) -> bool:
    print(f"[{index}/13] {label}")
    try:
        if action():
            capture(page, filename, modal=modal)
            return True
        print("       skipped: no existing read-only example available")
        return False
    except Exception as error:  # Optional feature captures must not stop the main set.
        print(f"       WARNING: skipped after capture error ({type(error).__name__})")
        return False


def run(headed: bool) -> int:
    ensure_dashboard()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    before = local_state_snapshot()
    saved = skipped = failed = 0
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=not headed)
        except Exception as error:
            raise SystemExit(f"Could not launch Chromium: {error}") from error
        context: BrowserContext = browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1,
            service_workers="block",
        )
        context.set_default_timeout(TIMEOUT_MS)
        context.route("**/*", request_guard)
        page = context.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        wait_for_dashboard(page)

        for number, (tab, filename, label) in enumerate(MAIN_SHOTS, 1):
            print(f"[{number}/13] {label}")
            try:
                navigate(page, tab)
                capture(page, filename, profile=tab == "profile")
                saved += 1
            except Exception as error:
                failed += 1
                print(f"       FAILED: {type(error).__name__}: {error}")

        # The remaining captures only open existing read-only views; no submit/generate action is used.
        navigate(page, "jobs")
        job_detail = page.locator('#tbody [data-detail]').first
        if job_detail.count():
            def open_job() -> bool:
                job_detail.click()
                page.locator("#application-dialog[open]").wait_for(timeout=TIMEOUT_MS)
                return True
            if optional(page, 9, "Job details", "09-job-details.png", open_job, modal="application"):
                saved += 1
                page.locator("#close-application").click()
        else:
            print("[9/13] Job details\n       skipped: no existing read-only example available")
            skipped += 1

        fit = page.locator('#tbody [data-score-detail]').first
        if fit.count():
            def open_fit() -> bool:
                fit.click()
                page.locator("#detail[open]").wait_for(timeout=TIMEOUT_MS)
                return page.locator("#copy-profile-analysis").is_visible()
            if optional(page, 10, "Profile Fit", "10-profile-fit.png", open_fit, modal="profile-fit"):
                saved += 1
                page.locator("#close-detail").click()
            else:
                skipped += 1
        else:
            print("[10/13] Profile Fit\n        skipped: no existing read-only example available")
            skipped += 1

        decision = page.locator('#tbody [data-application-decision]').first
        if decision.count():
            def open_decision() -> bool:
                decision.click()
                page.locator("#detail[open]").wait_for(timeout=TIMEOUT_MS)
                return True
            if optional(page, 11, "Application Decision", "11-application-decision.png", open_decision, modal="decision"):
                saved += 1
                page.locator("#close-detail").click()
            else:
                skipped += 1
        else:
            print("[11/13] Application Decision\n        skipped: no existing read-only example available")
            skipped += 1

        # Preview is explicitly read-only; no generate/reuse/regenerate button is activated.
        # Try existing jobs in the current result set until one exposes a saved package.
        page.locator("#page-size").select_option("100")
        page.wait_for_timeout(250)
        material_buttons = page.locator('#tbody [data-detail]')
        material_count = material_buttons.count()
        material_ok = False
        try:
            for material_index in range(material_count):
                page.locator('#tbody [data-detail]').nth(material_index).click()
                page.locator("#application-dialog[open]").wait_for(timeout=TIMEOUT_MS)
                trigger = page.locator('#application-content [data-generate-materials]').first
                if trigger.count():
                    trigger.click()
                    page.locator("#materials-dialog[open]").wait_for(timeout=TIMEOUT_MS)
                    page.locator("#materials-preview").wait_for(timeout=TIMEOUT_MS)
                    page.wait_for_timeout(350)
                    has_existing = page.locator("#materials-output .materials-output").count() > 0
                    if has_existing:
                        capture(page, "12-application-materials.png", modal="materials")
                        saved += 1; material_ok = True
                        page.locator("#close-materials").click()
                        page.locator("#close-application").click()
                        break
                    page.locator("#close-materials").click()
                page.locator("#close-application").click()
            if not material_ok:
                print("[12/13] Application Materials\n        skipped: no existing read-only example available")
                skipped += 1
        except Exception as error:
            print(f"[12/13] Application Materials\n        WARNING: skipped after capture error ({type(error).__name__})")
            skipped += 1
            for selector in ("#materials-dialog", "#application-dialog"):
                try:
                    page.locator(selector).evaluate("el => el.open && el.close()")
                except Exception:
                    pass

        navigate(page, "applications")
        tracking = page.locator('#tbody [data-detail]').first
        if tracking.count():
            def open_tracking() -> bool:
                tracking.click()
                page.locator("#application-dialog[open]").wait_for(timeout=TIMEOUT_MS)
                return True
            if optional(page, 13, "Application Tracking", "13-application-tracking.png", open_tracking, modal="application"):
                saved += 1
            else:
                skipped += 1
        else:
            print("[13/13] Application Tracking\n        skipped: no existing read-only example available")
            skipped += 1

        browser.close()

    after = local_state_snapshot()
    if before != after:
        failed += 1
        changed = sorted(set(before) | set(after))
        changed = [name for name in changed if before.get(name) != after.get(name)]
        print("WARNING: local SQLite artifacts changed during dashboard rendering: " + ", ".join(changed))
    print("\nScreenshots complete")
    print(f"Saved: {saved}\nSkipped: {skipped}\nFailed: {failed}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show the Chromium window while capturing")
    args = parser.parse_args()
    return run(args.headed)


if __name__ == "__main__":
    sys.exit(main())
