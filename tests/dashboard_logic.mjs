import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../dashboard_assets/logic.js', import.meta.url), 'utf8');
const {query, makeCsv, reviewQueueGroup} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const defaults = {runs:[],search:'',status:'',type:'',domain:'',from:'',to:'',alive:'',ai:'',hasEmails:'',hasJobs:'',hasCareers:'',hasError:'',email:'',job:'',url:'',minPages:'',maxPages:'',minJobs:'',maxJobs:'',minEmails:'',maxEmails:'',sort:'newest',preMatchStatus:'',qualityStatus:'',eligibilityStatus:'',recommendationStatus:'',userDecisionStatus:'',reviewQueueStatus:'',dedup:true,keep:'latest'};
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
const csv=makeCsv(['Title'],[['=1+1'],['a,"b"\nnew line']]);
assert.ok(csv.startsWith('\ufeff'));assert.ok(csv.includes("'=1+1"));assert.ok(csv.includes('"a,""b""\nnew line"'));
console.log('Dashboard query regression tests passed.');
