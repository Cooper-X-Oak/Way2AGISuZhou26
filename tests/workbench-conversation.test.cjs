const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
// Only the DOM-free helpers between the pure markers are evaluated.
const page=fs.readFileSync('pipeline/view_template.html','utf8');
const pure=page.slice(page.indexOf('/* pure:start'),page.indexOf('/* pure:end */'));
const ctx={};vm.createContext(ctx);vm.runInContext(pure+';this.groupTaskRecords=groupTaskRecords;this.taskRequestBody=taskRequestBody;',ctx);

test('reload groups parent chains into one conversation and retains unrelated tasks',()=>{
 const row=(id,parent,date)=>({run_id:id,task:{parent_id:parent,created_at:date,message:id}});
 const groups=ctx.groupTaskRecords([row('second','first','2'),row('other',null,'3'),row('first',null,'1'),row('third','second','4')]);
 assert.equal(groups.length,2);assert.equal(groups[0].root.run_id,'first');assert.equal(groups[0].latest.run_id,'third');
 assert.deepEqual([...groups[0].ids],['first','second','third']);
});

test('a follow-up continues the latest turn with the same product and area',()=>{
 const conv={id:'first',turns:['first','second'],area_id:'shishan',profile:'xinxian-huoquan'};
 const body=ctx.taskRequestBody({conv,view:null,text:'把半径改成 2 公里'});
 assert.equal(body.parent_id,'second');assert.equal(body.area,'shishan');assert.equal(body.profile,'xinxian-huoquan');
 assert.equal(body.input,'把半径改成 2 公里');assert.equal(body.mode,'live');
});

test('a new task uses the picked product and area and has no parent',()=>{
 const body=ctx.taskRequestBody({conv:{draft:true},text:'狮山 3 公里',profile:'jiangnan-gongquan',area:'guanqian'});
 assert.equal(body.parent_id,undefined);assert.equal(body.profile,'jiangnan-gongquan');assert.equal(body.area,'guanqian');
});


test('assistant markdown renders common blocks and never executes source HTML',()=>{
 const html=ctx.markdown('# 标题\n\n**重点**与 `code`\n\n1. 第一步\n2. 第二步\n\n| 项目 | 状态 |\n| --- | --- |\n| 计划 | 待验证 |\n\n```js\n<script>alert(1)</script>\n```');
 assert.match(html,/<h1>标题<\/h1>/);assert.match(html,/<strong>重点<\/strong>/);
 assert.match(html,/<ol>/);assert.match(html,/<table>/);assert.match(html,/&lt;script&gt;/);
 assert.doesNotMatch(html,/<script>/);
 const unsafe=ctx.markdown('<img src=x onerror=alert(1)> [bad](javascript:alert(1))');
 assert.doesNotMatch(unsafe,/<img|href="javascript:/);
});
