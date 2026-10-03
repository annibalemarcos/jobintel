"""Local dashboard with personal application tracking (stdlib only)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from email.parser import BytesParser
from email.policy import default as email_policy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from applications import ApplicationStore, ConflictError, enrich, job_key
from prematching import RULE_VERSION, add_prematch_to_data, run_pre_match
from profile_fit import ProfileFitBatch, attach_profile_fit
from job_description_quality import attach_job_description_quality
from ai_analysis_eligibility import attach_ai_analysis_eligibility
from application_decision import attach_application_decisions
from application_materials import (RULE_VERSION as MATERIALS_RULE_VERSION, generate_and_persist,
                                   preview_materials)
from profile_fit import current_model

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / 'dashboard_assets'
SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dashboard_assets', '.codex', '.agents'}


def split_values(value):
    return [s.strip() for s in (value or '').split(' ; ') if s.strip()]


def number(value):
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError):
        return 0


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def discover(root):
    """Walk outputs without following symlinks or exposing unrelated project files."""
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.')
                         and not (Path(folder) / d).is_symlink())
        if any(name in files for name in ('results_all.csv', 'results_valid.csv', 'checkpoint.json')):
            yield Path(folder)


def collect(root=ROOT):
    runs, records, warnings = [], [], []
    for folder in discover(root):
        run_id = folder.relative_to(root).as_posix() or '.'
        info = {}
        try:
            if (folder / 'run_info.json').exists():
                info = json.loads((folder / 'run_info.json').read_text(encoding='utf-8-sig'))
                if not isinstance(info, dict):
                    raise ValueError('metadados inválidos')
        except (OSError, ValueError) as e:
            warnings.append(f'{run_id}: não foi possível ler os metadados ({e}).')
            info = {}
        fallback_date = ''
        try:
            fallback_date = datetime.strptime(folder.name, '%Y-%m-%d_%H-%M-%S').isoformat()
        except ValueError:
            pass
        run_date = str(info.get('started_at') or fallback_date)
        ai = info.get('ai_enabled') if isinstance(info.get('ai_enabled'), bool) else None
        ai_available = info.get('ai_available') if isinstance(info.get('ai_available'), bool) else None
        rows = []
        try:
            # Never ingest results_valid alongside results_all: it is a subset.
            source = next((folder / name for name in ('results_all.csv', 'results_valid.csv')
                           if (folder / name).exists()), None)
            if source:
                raw_rows = read_csv(source)
                if raw_rows and 'Domain' not in raw_rows[0]:
                    raise ValueError('coluna Domain ausente')
                for row in raw_rows:
                    if not row.get('Domain', '').strip():
                        continue
                    urls, titles = split_values(row.get('Job URLs')), split_values(row.get('Job Titles'))
                    rows.append({
                        'domain': row['Domain'].strip(), 'name': row.get('Site Name') or row['Domain'],
                        'alive': str(row.get('Alive', '')).upper() == 'YES',
                        'status': row.get('Access Status') or 'UNKNOWN',
                        'canonical': row.get('Canonical URL') or '', 'type': row.get('Site Type') or 'Não classificado',
                        'description': row.get('Description') or '', 'emails': split_values(row.get('Emails encontrados')),
                        'careers': split_values(row.get('Careers URLs')), 'pages': number(row.get('Pages Crawled')),
                        'evidence': split_values(row.get('Evidence URLs')), 'error': row.get('Error') or '',
                        'jobs': [{'url': u, 'title': titles[i] if i < len(titles) else ''} for i, u in enumerate(urls)],
                    })
            else:
                raw_rows = json.loads((folder / 'checkpoint.json').read_text(encoding='utf-8-sig'))
                if not isinstance(raw_rows, list):
                    raise ValueError('checkpoint inválido')
                for row in raw_rows:
                    if not isinstance(row, dict) or not row.get('input_domain'):
                        continue
                    jobs = row.get('job_records') or [
                        {'url': u, 'title': (row.get('job_titles') or [])[i] if i < len(row.get('job_titles') or []) else ''}
                        for i, u in enumerate(row.get('job_urls') or [])]
                    rows.append({
                        'domain': str(row['input_domain']), 'name': row.get('site_name') or row['input_domain'],
                        'alive': bool(row.get('alive')), 'status': row.get('access_status') or 'UNKNOWN',
                        'canonical': row.get('canonical_url') or '', 'type': row.get('site_type') or 'Não classificado',
                        'description': row.get('description') or '', 'emails': row.get('emails') or [],
                        'careers': row.get('careers_urls') or [], 'pages': number(row.get('pages_crawled')),
                        'evidence': row.get('evidence_urls') or [], 'error': row.get('error') or '', 'jobs': jobs,
                    })
            # Structured CSV preserves URL/title pairing even when a title contains a separator.
            if (folder / 'jobs.csv').exists():
                job_map = {}
                for job in read_csv(folder / 'jobs.csv'):
                    domain, url = (job.get('Domain') or '').strip().lower(), (job.get('Job URL') or '').strip()
                    if domain and url:
                        description=job.get('Job Description') or ''
                        job_map.setdefault(domain, []).append({'url':url,'title':job.get('Job Title') or '',
                            'company':job.get('Company') or '','location':job.get('Location') or '',
                            'date_posted':job.get('Date Posted') or '', 'description':description,
                            'description_hash':job.get('Description Hash') or
                                (hashlib.sha256(description.encode('utf-8')).hexdigest() if description else '')})
                for row in rows:
                    if row['domain'].lower() in job_map:
                        row['jobs'] = job_map[row['domain'].lower()]
            for i, row in enumerate(rows):
                row.update(id=f'{run_id}#{i}', run_id=run_id, date=run_date, ai=ai,
                           ai_available=ai_available,
                           model=str(info.get('model') or ''), version=str(info.get('version') or ''),
                           input_file=str(info.get('input_file') or ''))
                # Collapse duplicate values inside one observation, not across runs.
                for field in ('emails', 'careers', 'evidence'):
                    row[field] = list(dict.fromkeys(str(v) for v in row[field] if v))
                unique_jobs = {}
                for job in row['jobs']:
                    if isinstance(job, dict) and job.get('url'):
                        url=str(job['url'])
                        value={key:job.get(key) for key in ('url','title','company','location','date_posted','description',
                            'description_hash','employment_type','contract_type','salary_min','salary_max','salary_period','currency') if job.get(key) is not None}
                        value['url']=url;value['title']=str(value.get('title') or '')
                        value['company']=str(value.get('company') or row.get('name') or '')
                        value['description']=str(value.get('description') or '')
                        value['description_hash']=str(value.get('description_hash') or
                            (hashlib.sha256(value['description'].encode('utf-8')).hexdigest() if value['description'] else ''))
                        old=unique_jobs.get(url)
                        if old is None: unique_jobs[url]=value
                        else:
                            for key,item in value.items():
                                if item not in ('',None) and old.get(key) in ('',None): old[key]=item
                row['jobs'] = list(unique_jobs.values())
            records.extend(rows)
        except (OSError, ValueError, TypeError, KeyError, csv.Error) as e:
            warnings.append(f'{run_id}: resultados não carregados ({e}). Atualize após a gravação terminar.')
            rows = []
        run = {'id': run_id, 'date': run_date, 'finished': str(info.get('finished_at') or ''),
               'ai': ai, 'model': str(info.get('model') or ''), 'version': str(info.get('version') or ''),
               'ai_available': ai_available,
               'completed': info.get('completed') if isinstance(info.get('completed'), bool) else None,
               'resumed': info.get('resumed') if isinstance(info.get('resumed'), bool) else False,
               'input': str(info.get('input_file') or ''), 'count': len(rows),
               'requested': info.get('domains_requested'), 'alive': sum(r['alive'] for r in rows),
               'jobs': sum(len(r['jobs']) for r in rows), 'emails': sum(len(r['emails']) for r in rows),
               'errors': sum(bool(r['error']) for r in rows), 'pages': sum(r['pages'] for r in rows)}
        runs.append(run)
    runs.sort(key=lambda r: (r['date'], r['id']), reverse=True)
    return {'runs': runs, 'records': records, 'warnings': warnings,
            'updated_at': datetime.now().astimezone().isoformat()}


class Handler(BaseHTTPRequestHandler):
    def _material_job(self,url):
        key=job_key(url)
        data=collect(self.server.data_root)
        data=enrich(data,self.server.store)
        data=attach_job_description_quality(data,self.server.store)
        data=attach_profile_fit(data,self.server.store)
        found=None;order=None
        for record in data.get('records',[]):
            for job in record.get('jobs',[]):
                try:matches=job_key(job.get('url'))==key
                except (TypeError,ValueError):matches=False
                candidate=(str(record.get('date','')),str(record.get('run_id','')),str(record.get('id','')))
                if matches and (order is None or candidate>order):found,order=job,candidate
        if found is None:raise ValueError('A vaga não foi encontrada nas ocorrências atuais.')
        found['key']=key
        return found

    @staticmethod
    def _material_preview_response(plan):
        keys=('job_title','company','profile_version_id','quality','profile_fit_available','model','language',
              'selected_language','cache_available','cached_generation_id','cached_material','history','regenerate',
              'new_calls','blocked','warning','confirmation_token','rule_version')
        return {key:plan.get(key) for key in keys}

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/profile':
            try: self.json_response(self.server.store.get_profile())
            except Exception: self.json_response({'error':'Não foi possível carregar o perfil.'},500)
            return
        if path == '/api/profile/resume':
            try:
                target=self.server.store.current_resume_path()
                if not target:
                    self.json_response({'error':'Nenhum currículo está associado ao perfil.'},404); return
                profile=self.server.store.get_profile()['profile']
                name=quote((profile.get('resume') or {}).get('original_name') or 'curriculo',safe='')
                self.respond(target.read_bytes(),'application/octet-stream',200,
                             {'Content-Disposition':f"attachment; filename*=UTF-8''{name}"})
            except Exception:
                self.json_response({'error':'Não foi possível carregar o currículo.'},500)
            return
        if path == '/api/data':
            try:
                data=enrich(collect(self.server.data_root),self.server.store)
                data=add_prematch_to_data(data,self.server.store)
                data=attach_job_description_quality(data,self.server.store)
                data=attach_ai_analysis_eligibility(data,self.server.store.get_profile(),self.server.store)
                data=attach_profile_fit(data,self.server.store)
                self.json_response(attach_application_decisions(data,self.server.store))
            except Exception:
                self.json_response({'error': 'Não foi possível ler os dados de acompanhamento.'}, 500)
            return
        if path == '/api/profile-fit/progress':
            task_id=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
            state=self.server.profile_fit_batch.progress(task_id)
            self.json_response(state or {'error':'Execução de análise não encontrada.'},200 if state else 404)
            return
        if path.startswith('/api/profile/'):
            self.json_response({'error':'Rota de perfil não encontrada.'},404)
            return
        files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                 '/logic.js': ('logic.js', 'text/javascript'), '/tracking.js': ('tracking.js', 'text/javascript'),
                 '/tracking.css': ('tracking.css', 'text/css'), '/style.css': ('style.css', 'text/css')}
        files['/profile.css']=('profile.css','text/css')
        files['/profile_io.js']=('profile_io.js','text/javascript')
        files['/profile_analysis_export.js']=('profile_analysis_export.js','text/javascript')
        files['/application_materials_io.js']=('application_materials_io.js','text/javascript')
        files['/application_decision.css']=('application_decision.css','text/css')
        if path not in files:
            self.send_error(404)
            return
        name, mime = files[path]
        self.respond((ASSETS / name).read_bytes(), mime + '; charset=utf-8')

    def mutate(self, delete=False):
        # Only same-origin JSON requests can write local personal data.
        expected_origin = 'http://' + self.headers.get('Host', '')
        if self.headers.get('Origin') != expected_origin or self.headers.get('X-Site-Intel') != 'dashboard':
            self.json_response({'error': 'Origem da solicitação inválida.'}, 403)
            return
        if urlsplit(self.path).path != '/api/applications':
            self.json_response({'error': 'Rota não encontrada.'}, 404)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 100000 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Solicitação inválida.')
            body = json.loads(self.rfile.read(size))
            if delete:
                self.server.store.delete(body.get('url'), body.get('version'))
            else:
                self.server.store.save(body)
            self.json_response({'ok': True})
        except ConflictError as e:
            self.json_response({'error': str(e)}, 409)
        except (ValueError, TypeError, AttributeError) as e:
            self.json_response({'error': str(e)}, 400)
        except Exception:
            self.json_response({'error': 'Não foi possível salvar. Os dados anteriores foram preservados.'}, 500)

    def do_POST(self):
        if urlsplit(self.path).path in ('/api/application-materials/preview','/api/application-materials/generate'):
            expected_origin='http://'+self.headers.get('Host','')
            if self.headers.get('Origin')!=expected_origin or self.headers.get('X-Site-Intel')!='dashboard':
                self.json_response({'error':'Origem da solicitação inválida.'},403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Solicitação de materiais inválida.')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('Solicitação de materiais inválida.')
                url=body.get('url')
                if not isinstance(url,str) or len(url)>4000:raise ValueError('URL da vaga inválida.')
                language=body.get('language','auto')
                regenerate=body.get('regenerate',False)
                if not isinstance(regenerate,bool):raise ValueError('Opção de regeneração inválida.')
                job=self._material_job(url)
                active=self.server.store.get_profile()
                plan=preview_materials(job,active,self.server.store,current_model(),language,regenerate)
                if urlsplit(self.path).path.endswith('/preview'):
                    self.json_response(self._material_preview_response(plan));return
                token=body.get('confirmation_token')
                if not isinstance(token,str) or token!=plan['confirmation_token']:
                    self.json_response({'error':'Os dados da prévia mudaram. Revise a prévia novamente.','changed':True},409);return
                if plan['blocked']:
                    self.json_response({'error':plan['warning']},400);return
                if plan['cached_material'] and not regenerate:
                    self.json_response({'generation':plan['cached_material'],'reused':True,'new_calls':0,
                        'rule_version':MATERIALS_RULE_VERSION});return
                generation=generate_and_persist(plan,self.server.store)
                self.json_response({'generation':generation,'reused':False,'new_calls':1,
                    'rule_version':MATERIALS_RULE_VERSION})
            except (ValueError,TypeError,json.JSONDecodeError) as error:
                self.json_response({'error':str(error)},400)
            except Exception:
                self.json_response({'error':'A geração falhou. Materiais anteriores foram preservados.'},502)
            return
        if urlsplit(self.path).path == '/api/application-decision':
            expected_origin='http://'+self.headers.get('Host','')
            if self.headers.get('Origin')!=expected_origin or self.headers.get('X-Site-Intel')!='dashboard':
                self.json_response({'error':'Origem da solicitação inválida.'},403); return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Solicitação de decisão inválida.')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('Solicitação de decisão inválida.')
                result=self.server.store.save_user_application_decision(body.get('url'),body.get('decision'))
                self.json_response(result)
            except ConflictError as error:
                self.json_response({'error':str(error)},409)
            except (ValueError,TypeError,AttributeError,json.JSONDecodeError) as error:
                self.json_response({'error':str(error)},400)
            except Exception:
                self.json_response({'error':'Não foi possível salvar a decisão manual.'},500)
            return
        if urlsplit(self.path).path in ('/api/profile-fit/preview','/api/profile-fit/start'):
            expected_origin='http://'+self.headers.get('Host','')
            if self.headers.get('Origin')!=expected_origin or self.headers.get('X-Site-Intel')!='dashboard':
                self.json_response({'error':'Origem da solicitação inválida.'},403); return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Solicitação de análise inválida.')
                body=json.loads(self.rfile.read(size))
                urls=body.get('urls') if isinstance(body,dict) else None
                if not isinstance(urls,list) or len(urls)>1000 or any(not isinstance(url,str) or len(url)>4000 for url in urls):
                    raise ValueError('A lista de vagas é inválida.')
                if urlsplit(self.path).path.endswith('/preview'):
                    self.json_response(self.server.profile_fit_batch.preview(urls))
                else:
                    expected_new=body.get('expected_new')
                    if not isinstance(expected_new,int) or isinstance(expected_new,bool) or expected_new<0:
                        raise ValueError('Confirmação de novas chamadas inválida.')
                    token=body.get('plan_token')
                    if not isinstance(token,str) or len(token)!=64:raise ValueError('Prévia da análise inválida; confira novamente antes de iniciar.')
                    result=self.server.profile_fit_batch.start(urls,expected_new,token)
                    self.json_response(result,409 if result.get('changed') else 202)
            except (ValueError,TypeError,json.JSONDecodeError) as e:
                self.json_response({'error':str(e)},400)
            except RuntimeError as e:
                self.json_response({'error':str(e)},400)
            except Exception:
                self.json_response({'error':'Não foi possível iniciar a análise de perfil.'},500)
            return
        if urlsplit(self.path).path == '/api/pre-match':
            expected_origin='http://'+self.headers.get('Host','')
            if self.headers.get('Origin')!=expected_origin or self.headers.get('X-Site-Intel')!='dashboard':
                self.json_response({'error':'Origem da solicitação inválida.'},403); return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=100000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('Solicitação de pré-match inválida.')
                body=json.loads(self.rfile.read(size))
                urls=body.get('urls') if isinstance(body,dict) else None
                if not isinstance(urls,list) or len(urls)>1000 or any(not isinstance(url,str) or len(url)>4000 for url in urls):
                    raise ValueError('A lista de vagas é inválida.')
                result=run_pre_match(collect(self.server.data_root),self.server.store,urls)
                self.json_response(result)
            except (ValueError,TypeError,json.JSONDecodeError) as e:
                self.json_response({'error':str(e)},400)
            except Exception:
                self.json_response({'error':'Não foi possível executar o pré-match local.'},500)
            return
        if urlsplit(self.path).path != '/api/profile':
            self.mutate(); return
        expected_origin='http://'+self.headers.get('Host','')
        if self.headers.get('Origin')!=expected_origin or self.headers.get('X-Site-Intel')!='dashboard':
            self.json_response({'error':'Origem da solicitação inválida.'},403); return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=16*1024*1024: raise ValueError('Solicitação inválida ou arquivo maior que 15 MB.')
            content_type=self.headers.get('Content-Type','')
            if not content_type.lower().startswith('multipart/form-data;'): raise ValueError('Formato do formulário inválido.')
            raw=self.rfile.read(size)
            message=BytesParser(policy=email_policy).parsebytes(
                b'Content-Type: '+content_type.encode('ascii')+b'\r\nMIME-Version: 1.0\r\n\r\n'+raw)
            profile=None; upload=None
            for part in message.iter_parts():
                field=part.get_param('name',header='content-disposition')
                payload=part.get_payload(decode=True) or b''
                if field=='profile': profile=json.loads(payload.decode('utf-8'))
                elif field=='resume' and payload:
                    upload=(part.get_filename() or '',part.get_content_type(),payload)
            self.json_response(self.server.store.save_profile(profile,upload))
        except (ValueError,TypeError,AttributeError,json.JSONDecodeError) as e:
            self.json_response({'error':str(e)},400)
        except Exception:
            self.json_response({'error':'Não foi possível salvar o perfil. Versões anteriores foram preservadas.'},500)

    def do_DELETE(self):
        self.mutate(delete=True)

    def json_response(self, value, status=200):
        self.respond(json.dumps(value, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8', status)

    def respond(self, body, mime, status=200, extra_headers=None):
        try:
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            for name,value in (extra_headers or {}).items(): self.send_header(name,value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The client went away while the response was being written. The
            # connection is no longer usable, so do not let route handlers try
            # to send a second (usually 500) response.
            return

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description='Dashboard local do JobIntel')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-root', type=Path, default=ROOT,
                        help='Pasta que contém as saídas do crawler (inclui subpastas)')
    parser.add_argument('--tracking-db', type=Path, default=ROOT / '.dashboard_data' / 'applications.sqlite3',
                        help='Banco local de candidaturas (independente das coletas)')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.data_root = args.data_root.resolve()
    server.store = ApplicationStore(args.tracking_db.resolve())
    server.profile_fit_batch = ProfileFitBatch(server.store,lambda: enrich(collect(server.data_root),server.store))
    print(f'Dashboard: http://127.0.0.1:{args.port}', flush=True)
    print('Leitura das execuções em: ' + str(server.data_root), flush=True)
    print('Ctrl+C para encerrar.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
