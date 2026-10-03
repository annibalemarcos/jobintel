import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {exportProfileJson,importProfileJson} from '../dashboard_assets/profile_io.js';

const current={desired_roles:['Engenheira de protocolo'],interests:['DeFi'],seniority:'Sênior',
  work_modes:{remote:'preferred',hybrid:'acceptable',onsite:'unwanted'},related_roles_open:true,
  salary_min:'100000',analysis_notes:'Prefiro equipes distribuídas.',resume:{original_name:'cv.pdf',type:'application/pdf',
    uploaded_at:'2026-10-03T12:00:00Z',profile_version:2,storage_key:'local-user/private.pdf'}};
const state={profile:current,version:3,updated_at:'2026-10-03T13:00:00Z'};
const json=exportProfileJson(state,'2026-10-03T14:00:00Z');
const document=JSON.parse(json);
assert.equal(document.format,'crypto-site-intel-profile');
assert.equal(document.schema_version,1);
assert.equal(document.profile_version,3);
assert.equal(document.exported_at,'2026-10-03T14:00:00Z');
assert.equal(document.profile.desired_roles[0],'Engenheira de protocolo');
assert.equal(document.profile.resume.original_name,'cv.pdf');
assert.equal(Object.hasOwn(document.profile.resume,'storage_key'),false);
assert.equal(json.includes('local-user/private.pdf'),false);

const imported=importProfileJson(json,current);
assert.deepEqual(imported.desired_roles,current.desired_roles);
assert.equal(imported.analysis_notes,current.analysis_notes);
assert.deepEqual(imported.work_modes,current.work_modes);
assert.deepEqual(imported.resume,current.resume,'JSON metadata cannot replace the physical resume association');
assert.deepEqual(current.desired_roles,['Engenheira de protocolo'],'staging import must not mutate saved state');

const withUnknown=JSON.parse(json);withUnknown.profile.unknown_field='ignored';withUnknown.profile.resume.storage_key='../../outside.pdf';
assert.deepEqual(importProfileJson(JSON.stringify(withUnknown),current).desired_roles,current.desired_roles);
assert.throws(()=>importProfileJson('{invalid',current),/JSON válido/);
assert.throws(()=>importProfileJson(JSON.stringify({...document,schema_version:99}),current),/versão incompatível/);
assert.throws(()=>importProfileJson(JSON.stringify({...document,profile:{...document.profile,desired_roles:[42]}}),current),/lista de textos/);

const appSource=readFileSync(new URL('../dashboard_assets/app.js',import.meta.url),'utf8');
const importHandler=appSource.slice(appSource.indexOf("$('profile-json-file').onchange"),appSource.indexOf("$('profile-form').addEventListener('submit'"));
assert.ok(importHandler.includes('renderProfileForm(staged)'));
assert.ok(!importHandler.includes('fetch('),'Import must stage form values without saving automatically');
console.log('Profile JSON import/export tests passed.');
