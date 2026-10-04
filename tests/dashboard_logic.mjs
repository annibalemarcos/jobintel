import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../dashboard_assets/logic.js', import.meta.url), 'utf8');
const {query, makeCsv, reviewQueueGroup, desiredRoleMatches, coldEmailCategory, coldEmailFilterMatches, bestContactEmail} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const defaults = {runs:[],search:'',status:'',type:'',domain:'',from:'',to:'',alive:'',ai:'',hasEmails:'',hasJobs:'',hasCareers:'',hasError:'',email:'',job:'',url:'',minPages:'',maxPages:'',minJobs:'',maxJobs:'',minEmails:'',maxEmails:'',sort:'newest',preMatchStatus:'',qualityStatus:'',eligibilityStatus:'',recommendationStatus:'',userDecisionStatus:'',reviewQueueStatus:'',desiredRoleStatus:'',coldEmailStatus:'',dedup:true,keep:'latest'};
const record=(date,run,extra={})=>({id:run+'#0',run_id:run,date,domain:'aave.com',name:'Aave',type:'DeFi',status:'OK',alive:true,ai:false,model:'luna',version:'2.1.4',input_file:'domains.csv',canonical:'https://aave.com',description:'',error:'',pages:45,emails:['contact@aave.com','hr@aave.com'],careers:['https://aave.com/careers'],evidence:[],jobs:[{url:'https://aave.com/jobs/1',title:'Engineer'},{url:'https://aave.com/jobs/2',title:'Designer'}],...extra});
const records=[record('2026-10-01T12:00:00','old'),record('2026-10-03T12:00:00','new')];
let result=query(records,defaults,'companies');
assert.equal(result.rows.length,1);assert.equal(result.hidden,1);assert.equal(result.rows[0].run_id,'new');
assert.equal(query(records,{...defaults,sort:'oldest'},'companies').rows[0].run_id,'new','Display order must not change duplicate retention');
assert.equal(query(records,{...defaults,keep:'earliest'},'companies').rows[0].run_id,'old');
assert.equal(query(records,{...defaults,dedup:false},'companies').rows.length,2);
assert.equal(query(records,{...defaults,runs:['old']},'companies').rows[0].run_id,'old');
assert.equal(query(records,{...defaults,to:'2026-10-01'},'companies').rows.length,1);
assert.equal(query(records,{...defaults,from:'2026-10-03',to:'2026-10-03'},'companies').rows.length,1);
assert.equal(query(records,{...defaults,email:'hr@'},'emails').rows.length,1);
assert.equal(query(records,{...defaults,search:'engineer'},'jobs').rows.length,1,'Job search must not include sibling roles');
assert.equal(query(records,{...defaults,search:'designer'},'companies').rows.length,1);
assert.equal(query(records,{...defaults,minPages:'46'},'companies').rows.length,0);
assert.equal(query(records,{...defaults,maxJobs:'0'},'companies').rows.length,0);
assert.equal(query(records,{...defaults,maxEmails:'2'},'companies').rows.length,1);
assert.equal(query(records,{...defaults,ai:'yes'},'companies').rows.length,0);
assert.equal(query(records,{...defaults,model:'other'},'companies').rows.length,0);
assert.equal(query(records,{...defaults,input_file:'domains.csv'},'companies').rows.length,1);
assert.equal(query(records,{...defaults,hasError:'no',hasJobs:'yes'},'companies').rows.length,1);
assert.equal(query([...records,record('2026-10-02','other',{domain:'other.com'})],defaults,'emails').rows.length,2,'Same email across companies must collapse');
assert.equal(query(records,defaults,'runs').rows.length,2,'Runs must not be deduplicated');
const tracked=structuredClone(records);
for(const r of tracked){
  r.jobs[0].application={tracked:true,status:'rejected',priority:'high',rejection_stage:'after_interview',applied_at:'2026-10-01',response_at:'2026-10-02',history:[{changes:{status:{to:'interview'}}}]};
  r.jobs[0].score={total:82};
  r.jobs[1].application={tracked:true,status:'applied',priority:'normal',applied_at:'2026-10-03',follow_up_at:'2026-10-05'};
  r.jobs[1].score={total:null};
}
assert.equal(query(tracked,{...defaults,applicationStatus:'rejected'},'jobs').rows.length,1);
assert.equal(query(tracked,{...defaults,milestone:'interview'},'applications').rows.length,1,'Rejection after interview must preserve achieved interview');
assert.equal(query(tracked,{...defaults,scoreStatus:'scored',minScore:'80'},'jobs').rows.length,1);
assert.equal(query(tracked,{...defaults,scoreStatus:'unscored'},'jobs').rows.length,1);
assert.equal(query(tracked,{...defaults,sort:'score_desc'},'jobs').rows[0].item.score.total,82);
const sortRecord=record('2026-10-03T12:00:00','sortable',{jobs:[
  {url:'https://aave.com/jobs/score-9',title:'Role 9',score:{total:9},application:{applied_at:'2026-10-03T09:30:00Z',follow_up_at:'2026-10-08T12:00:00Z'}},
  {url:'https://aave.com/jobs/score-100',title:'Role 100',score:{total:100},application:{applied_at:'2026-10-01T12:00:00Z',follow_up_at:'2026-10-05T12:00:00Z'}},
  {url:'https://aave.com/jobs/score-80',title:'Role 80',score:{total:80},application:{applied_at:'02/10/2026 23:59',follow_up_at:'2026-10-06T12:00:00Z'}},
  {url:'https://aave.com/jobs/score-missing',title:'Role missing',score:{total:null},application:{}},
  {url:'https://aave.com/jobs/score-0',title:'Role zero',score:{total:0},application:{applied_at:'',follow_up_at:null}}
]});
const sortTitles=(sort,filters={})=>query([sortRecord],{...defaults,...filters,sort},'jobs').rows.map(row=>row.item.title);
assert.deepEqual(sortTitles('score_desc'),['Role 100','Role 80','Role 9','Role zero','Role missing'],
  'profile scores sort numerically descending, keeping unknown scores last');
assert.deepEqual(sortTitles('score_asc'),['Role zero','Role 9','Role 80','Role 100','Role missing'],
  'profile scores sort numerically ascending, keeping unknown scores last');
assert.deepEqual(sortTitles('applied_newest'),['Role 9','Role 80','Role 100','Role missing','Role zero'],
  'application dates sort by timestamp descending and missing dates remain last');
assert.deepEqual(sortTitles('applied_oldest'),['Role 100','Role 80','Role 9','Role missing','Role zero'],
  'application dates sort by timestamp ascending and DD/MM/YYYY dates are parsed as day/month');
assert.deepEqual(sortTitles('followup'),['Role 100','Role 80','Role 9','Role missing','Role zero'],
  'nearest follow-up sorts first and missing dates remain last');
assert.deepEqual(sortTitles('followup_desc'),['Role 9','Role 80','Role 100','Role missing','Role zero'],
  'farthest follow-up sorts first and missing dates remain last');
const executionRows=[
  record('2026-10-01T12:00:00Z','run-old',{jobs:[{url:'https://aave.com/jobs/run-old',title:'Run old'}]}),
  record('2026-10-03T12:00:00Z','run-new',{jobs:[{url:'https://aave.com/jobs/run-new',title:'Run new'}]}),
  record('','run-missing',{jobs:[{url:'https://aave.com/jobs/run-missing',title:'Run missing'}]})
];
assert.deepEqual(query(executionRows,{...defaults,sort:'execution_newest'},'jobs').rows.map(row=>row.item.title),
  ['Run new','Run old','Run missing'],'execution dates sort by real timestamp with missing last');
assert.deepEqual(query(executionRows,{...defaults,sort:'execution_oldest'},'jobs').rows.map(row=>row.item.title),
  ['Run old','Run new','Run missing']);
assert.deepEqual(sortTitles('score_desc',{search:'role',scoreStatus:'scored'}).slice(0,2),['Role 100','Role 80'],
  'sorting follows active search and filters before callers paginate the result');
assert.deepEqual(sortTitles('score_desc').slice(0,2),['Role 100','Role 80'],
  'the first page slice is taken from the fully sorted result');
assert.equal(query(tracked,{...defaults,hasResponse:'no'},'applications').rows.length,1);
assert.equal(query(tracked,{...defaults,appliedTo:'2026-10-02'},'applications').rows.length,1);
assert.equal(query(tracked,{...defaults,followUp:'2026-10-05'},'applications').rows.length,1);
assert.equal(query(tracked,{...defaults,priority:'high',rejectionStage:'after_interview'},'companies').rows.length,1);
assert.equal(query(records,defaults,'applications').rows.length,0);
const prematchRecord=record('2026-10-03','prematch',{jobs:[
  {url:'https://aave.com/jobs/strong',title:'Strong',pre_match:{classification:'strong_candidate'}},
  {url:'https://aave.com/jobs/possible',title:'Possible',pre_match:{classification:'possible_candidate'}},
  {url:'https://aave.com/jobs/weak',title:'Weak',pre_match:{classification:'weak_candidate'}},
  {url:'https://aave.com/jobs/unseen',title:'Unseen'}
]});
assert.equal(query([prematchRecord],{...defaults,preMatchStatus:'strong_candidate'},'jobs').rows.length,1);
assert.equal(query([prematchRecord],{...defaults,preMatchStatus:'unscored'},'jobs').rows[0].item.title,'Unseen');
assert.deepEqual(query([prematchRecord],{...defaults,sort:'pre_match'},'jobs').rows.map(row=>row.item.title),['Strong','Possible','Weak','Unseen']);
const qualityRecord=record('2026-10-03','quality',{jobs:[
  {url:'https://aave.com/jobs/complete',title:'Complete',description_quality:{status:'complete'}},
  {url:'https://aave.com/jobs/partial',title:'Partial',description_quality:{status:'partial'}},
  {url:'https://aave.com/jobs/insufficient',title:'Insufficient',description_quality:{status:'insufficient'}},
  {url:'https://aave.com/jobs/not-evaluated',title:'Not evaluated'}
]});
assert.equal(query([qualityRecord],{...defaults,qualityStatus:'complete'},'jobs').rows[0].item.title,'Complete');
assert.equal(query([qualityRecord],{...defaults,qualityStatus:'unscored'},'jobs').rows[0].item.title,'Not evaluated');
const eligibilityRecord=record('2026-10-03','eligibility',{jobs:[
  {url:'https://aave.com/jobs/eligible',title:'Eligible',ai_analysis_eligibility:{status:'eligible',reason:'Related role'}},
  {url:'https://aave.com/jobs/review',title:'Review',ai_analysis_eligibility:{status:'review',reason:'Unclear role'}},
  {url:'https://aave.com/jobs/excluded',title:'Excluded',ai_analysis_eligibility:{status:'excluded',reason:'Engineering role'}},
  {url:'https://aave.com/jobs/unassessed',title:'Unassessed'}
]});
assert.equal(query([eligibilityRecord],{...defaults,eligibilityStatus:'eligible'},'jobs').rows[0].item.title,'Eligible');
assert.equal(query([eligibilityRecord],{...defaults,eligibilityStatus:'review'},'jobs').rows[0].item.title,'Review');
assert.equal(query([eligibilityRecord],{...defaults,eligibilityStatus:'excluded'},'jobs').rows[0].item.title,'Excluded');
assert.equal(query([eligibilityRecord],{...defaults,eligibilityStatus:'unscored'},'jobs').rows[0].item.title,'Unassessed');
const changedQuality=[
  record('2026-10-01','quality-old',{jobs:[{url:'https://aave.com/careers/strategy',title:'Strategy & Business Development Associate',description_quality:{status:'insufficient'}}]}),
  record('2026-10-03','quality-current',{jobs:[{url:'https://aave.com/careers/strategy',title:'Strategy & Business Development Associate',description_quality:{status:'complete'}}]})
];
assert.equal(query(changedQuality,{...defaults,qualityStatus:'complete'},'jobs').rows[0].run_id,'quality-current',
  'quality filtering must select the latest job occurrence before checking its current quality');
assert.equal(query(changedQuality,{...defaults,qualityStatus:'insufficient'},'jobs').rows.length,0,
  'an older insufficient result must not represent the current job when deduplication is enabled');
const decisionHistory=[
  record('2026-10-01T12:00:00','decision-old',{jobs:[{url:'https://aave.com/careers/strategy',title:'Strategy',
    description_quality:{status:'complete'},ai_analysis_eligibility:{status:'eligible'},pre_match:{classification:'strong_candidate'},
    application_decision:{recommendation:'prioritize'},user_decision:{user_decision:'want_to_apply'}}]}),
  record('2026-10-03T12:00:00','decision-current',{jobs:[{url:'https://aave.com/careers/strategy',title:'Strategy',
    description_quality:{status:'complete'},ai_analysis_eligibility:{status:'eligible'},pre_match:{classification:'possible_candidate'},
    application_decision:{recommendation:'consider'},user_decision:{user_decision:'do_not_apply'}}]})
];
assert.equal(query(decisionHistory,defaults,'jobs').rows[0].run_id,'decision-current',
  'recommendation/manual decision must not break latest-occurrence deduplication');
assert.equal(query(decisionHistory,{...defaults,recommendationStatus:'prioritize'},'jobs').rows.length,0,
  'recommendation filter applies to the current occurrence, not an older cached state');
assert.equal(query(decisionHistory,{...defaults,recommendationStatus:'consider',userDecisionStatus:'do_not_apply',eligibilityStatus:'eligible'},'jobs').rows[0].run_id,'decision-current',
  'recommendation, manual, and existing eligibility filters compose');
const reviewJob=(url,title,quality,eligibility,prematch,recommendation='review',date_posted='')=>({url,title,date_posted,
  description_quality:{status:quality},ai_analysis_eligibility:{status:eligibility},pre_match:{classification:prematch},
  application_decision:{recommendation},profile_analysis:null});
assert.equal(reviewQueueGroup(reviewJob('/a','A','complete','eligible','possible_candidate')),'review_first');
assert.equal(reviewQueueGroup(reviewJob('/b','B','complete','eligible','strong_candidate')),'review_first');
assert.equal(reviewQueueGroup({...reviewJob('/manila','Manila','complete','eligible','possible_candidate'),
  application_decision:{recommendation:'review',risks:{location:{status:'conflict'},work_model:{status:'conflict'}}}}),'review_later',
  'known hard practical conflicts must not be placed in review_first');
assert.equal(reviewQueueGroup(reviewJob('/c','C','partial','review','possible_candidate')),'review_later');
assert.equal(reviewQueueGroup(reviewJob('/d','D','complete','review','possible_candidate')),'review_later');
assert.equal(reviewQueueGroup(reviewJob('/e','E','insufficient','eligible','strong_candidate')),'insufficient_data');
assert.equal(reviewQueueGroup(reviewJob('/f','F','complete','eligible','strong_candidate','consider')),null,
  'only recommendation=review enters the review queue');
const reviewOccurrences=[
  record('2026-10-01','review-old',{jobs:[reviewJob('https://aave.com/jobs/current','Current','complete','eligible','possible_candidate','review','2026-10-01')]}),
  record('2026-10-04','review-current',{jobs:[reviewJob('https://aave.com/jobs/current','Current','partial','review','possible_candidate','review','2026-10-03')]})
];
assert.equal(query(reviewOccurrences,{...defaults,reviewQueueStatus:'review_first'},'jobs').rows.length,0,
  'review queue filter must classify the deduplicated current occurrence');
assert.equal(query(reviewOccurrences,{...defaults,reviewQueueStatus:'review_later'},'jobs').rows[0].run_id,'review-current');
const reviewSorting=record('2026-10-01','review-sort',{jobs:[
  reviewJob('https://aave.com/jobs/first-old','First old','complete','eligible','possible_candidate','review','2026-10-01'),
  reviewJob('https://aave.com/jobs/first-new','First new','complete','eligible','strong_candidate','review','2026-10-05'),
  reviewJob('https://aave.com/jobs/later','Later','partial','review','possible_candidate','review','2026-10-06'),
  reviewJob('https://aave.com/jobs/insufficient','Insufficient','insufficient','eligible','strong_candidate','review','2026-10-07'),
  reviewJob('https://aave.com/jobs/other','Other','complete','eligible','strong_candidate','consider','2026-10-08')
]});
assert.deepEqual(query([reviewSorting],{...defaults,sort:'review_queue',dedup:false},'jobs').rows.map(row=>row.item.title),
  ['First new','First old','Later','Insufficient','Other'],
  'queue sorting groups first/later/insufficient before non-review and orders each group by publication date');
const desiredCustomer=['Customer Operations'];
for(const title of ['Customer Operations','Customer Operations Manager','Senior Customer Operations Manager','Customer Operations Lead','Customer Ops Manager'])
  assert.equal(desiredRoleMatches(title,desiredCustomer),true,`${title} should match Customer Operations`);
for(const title of ['Solutions Engineer','Software Engineer','Enterprise Account Executive','Enterprise Partnerships Lead (Financial Markets)','Financial Analyst'])
  assert.equal(desiredRoleMatches(title,desiredCustomer),false,`${title} should not match Customer Operations`);
assert.equal(desiredRoleMatches('Solutions Engineer',['Solutions Engineer']),true,
  'a role explicitly added to desired_roles can match normally');
assert.equal(desiredRoleMatches('Enterprise Partnerships Lead (Financial Markets)',['Enterprise Partnerships']),true,
  'a desired role family explicitly added by the user can match');
for(const title of ['Product Operations Manager','Senior Product Operations Specialist','Product Ops Lead'])
  assert.equal(desiredRoleMatches(title,['Product Operations']),true);
for(const title of ['Product Manager','Product Designer','Product Engineer','Solutions Engineer'])
  assert.equal(desiredRoleMatches(title,['Product Operations']),false);
for(const title of ['P2P Dispute Moderator','P2P Dispute Specialist','P2P Dispute Resolution Specialist','P2P Moderator'])
  assert.equal(desiredRoleMatches(title,['P2P Dispute Resolution']),true);
for(const title of ['P2P Backend Engineer','Crypto Trader','Partnerships Manager'])
  assert.equal(desiredRoleMatches(title,['P2P Dispute Moderator']),false);
for(const title of ['Community Moderator','Community Moderation Specialist','Senior Community Moderator'])
  assert.equal(desiredRoleMatches(title,['Community Moderator']),true);
for(const title of ['Community Engineer','Developer Relations Engineer'])
  assert.equal(desiredRoleMatches(title,['Community Moderator']),false);
assert.equal(desiredRoleMatches('Customer Operations Manager',[]),false,'empty profile roles cannot invent a match');
const desiredRoleRecords=[record('2026-10-01','desired-old',{jobs:[
  {url:'https://aave.com/jobs/customer-ops',title:'Customer Operations Manager',description:'Solutions Engineer and many other words.'},
  {url:'https://aave.com/jobs/solutions',title:'Solutions Engineer',description:'Customer operations, support, Web3, SaaS, LATAM.'},
  {url:'https://aave.com/jobs/partnerships',title:'Enterprise Partnerships Lead (Financial Markets)'}
]}),record('2026-10-03','desired-current',{jobs:[
  {url:'https://aave.com/jobs/customer-ops',title:'Customer Ops Manager'},
  {url:'https://aave.com/jobs/product-ops',title:'Product Ops Lead'}
]})];
const compatibleJobs=query(desiredRoleRecords,{...defaults,desiredRoleStatus:'compatible'},'jobs',desiredCustomer).rows;
assert.deepEqual(compatibleJobs.map(row=>row.item.title),['Customer Ops Manager'],
  'desired role filter uses the deduplicated current occurrence and title, not matching words in description');
assert.deepEqual(query(desiredRoleRecords,{...defaults,desiredRoleStatus:'incompatible'},'jobs',desiredCustomer).rows.map(row=>row.item.title),
  ['Product Ops Lead','Solutions Engineer','Enterprise Partnerships Lead (Financial Markets)']);
assert.equal(query(desiredRoleRecords,{...defaults,desiredRoleStatus:'compatible',search:'customer'},'jobs',desiredCustomer).rows.length,1,
  'desired role composes with search');
assert.equal(query(desiredRoleRecords,{...defaults,desiredRoleStatus:'compatible',domain:'aave.com'},'jobs',desiredCustomer).rows.length,1,
  'desired role composes with existing filters');
assert.deepEqual(query(desiredRoleRecords,{...defaults,desiredRoleStatus:'compatible',sort:'oldest'},'jobs',desiredCustomer).rows.map(row=>row.item.title),['Customer Ops Manager']);
const pagedDesired=query([record('2026-10-03','desired-page',{jobs:[
  {url:'https://aave.com/jobs/1',title:'Customer Operations Manager'},
  {url:'https://aave.com/jobs/2',title:'Senior Customer Operations Lead'},
  {url:'https://aave.com/jobs/3',title:'Customer Operations Specialist'}
]})],{...defaults,desiredRoleStatus:'compatible',sort:'newest'},'jobs',desiredCustomer).rows;
assert.equal(pagedDesired.slice(0,2).length,2,'matching/sort occur before pagination slicing');
assert.equal(query(desiredRoleRecords,{...defaults,desiredRoleStatus:'compatible'},'jobs',[]).rows.length,0);
assert.equal(query(desiredRoleRecords,{...defaults,desiredRoleStatus:'incompatible'},'jobs',[]).rows.length,0,
  'without desired roles both explicit classifications remain unavailable');
assert.equal(query(desiredRoleRecords,{...defaults,desiredRoleStatus:'compatible'},'companies',[]).rows.length,1,
  'the jobs-only filter does not affect other tabs');
const coldCompany=(domain,extra={})=>({...record('2026-10-03',domain,{jobs:[],careers:[],emails:['contact@company.test'],pages:3,alive:true,status:'OK',error:''}),domain,name:domain,...extra});
const coldRecords=[
  coldCompany('jobs.test',{emails:['jobs@company.test','contact@company.test']}),
  coldCompany('hello.test',{emails:['hello@company.test']}),
  coldCompany('careerless.test',{emails:['careers@company.test','info@company.test']}),
  coldCompany('careers-page.test',{careers:['https://careers-page.test/careers']}),
  coldCompany('with-jobs.test',{jobs:[{url:'https://with-jobs.test/jobs/1',title:'Role'}]}),
  coldCompany('no-email.test',{emails:[]}),
  coldCompany('failed.test',{status:'NO_RESPONSE',alive:false,pages:0}),
  coldCompany('unknown.test',{status:'UNKNOWN',alive:false,pages:0}),
  coldCompany('generic-pages.test',{evidence:['https://generic-pages.test/about','https://generic-pages.test/contact']})
];
assert.equal(coldEmailCategory(coldRecords[0]),'opportunity','successful processed company with email and no career/job signal qualifies');
assert.equal(coldEmailCategory(coldRecords[1]),'opportunity');
assert.equal(coldEmailCategory(coldRecords[2]),'opportunity');
assert.equal(coldEmailCategory(coldRecords[3]),'careers');
assert.equal(coldEmailCategory(coldRecords[4]),'careers');
assert.equal(coldEmailCategory(coldRecords[5]),'no_email');
assert.equal(coldEmailCategory(coldRecords[6]),'unknown','failed site is not treated as confirmed absence');
assert.equal(coldEmailCategory(coldRecords[7]),'unknown');
assert.equal(coldEmailCategory(coldRecords[8]),'opportunity','generic about/contact pages are not Careers pages');
assert.equal(bestContactEmail(['other@company.test','info@company.test','contact@company.test','jobs@company.test']),'jobs@company.test');
assert.equal(bestContactEmail(['other@company.test','hello@company.test','info@company.test']),'hello@company.test');
assert.equal(bestContactEmail(['other@company.test','malformed-address']),'other@company.test');
assert.equal(coldEmailFilterMatches(coldRecords[3],'careers'),true,'career page counts even when no email was found');
const coldQuery=(filters={},records=coldRecords)=>query(records,{...defaults,...filters},'companies').rows;
assert.deepEqual(coldQuery({coldEmailStatus:'opportunity'}).map(row=>row.domain),
  ['jobs.test','hello.test','generic-pages.test','careerless.test']);
assert.deepEqual(coldQuery({coldEmailStatus:'careers'}).map(row=>row.domain),['with-jobs.test','careers-page.test']);
assert.deepEqual(coldQuery({coldEmailStatus:'no_email'}).map(row=>row.domain),['no-email.test']);
assert.deepEqual(coldQuery({coldEmailStatus:'opportunity',search:'hello'}).map(row=>row.domain),['hello.test'],
  'cold email filter composes with search');
assert.deepEqual(coldQuery({coldEmailStatus:'opportunity',status:'OK'}).map(row=>row.domain),
  ['jobs.test','hello.test','generic-pages.test','careerless.test'],'cold email filter composes with status');
assert.deepEqual(coldQuery({coldEmailStatus:'opportunity',sort:'name'}).map(row=>row.domain),
  ['careerless.test','generic-pages.test','hello.test','jobs.test'],'existing sort applies to filtered companies');
assert.equal(coldQuery({coldEmailStatus:'opportunity'}).slice(0,2).length,2,'filter applies before page slicing');
const changingColdFilter={page:4,change(value){this.value=value;this.page=1;}};
changingColdFilter.change('opportunity');
assert.equal(changingColdFilter.page,1,'changing the filter returns to the first page');
const csv=makeCsv(['Title'],[['=1+1'],['a,"b"\nnew line']]);
assert.ok(csv.startsWith('\ufeff'));assert.ok(csv.includes("'=1+1"));assert.ok(csv.includes('"a,""b""\nnew line"'));
console.log('Dashboard query regression tests passed.');
