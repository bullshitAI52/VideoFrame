const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('static/app.js','utf8');
const listeners={};
let editing=true, renders=0, next={directory:'/new',sheets:[]};
const context=vm.createContext({
 document:{addEventListener:(name,fn)=>listeners[name]=fn,activeElement:{matches:()=>editing},querySelector:()=>null},
 fetch:async()=>({ok:true,json:async()=>structuredClone(next)}),
 setInterval:()=>{},setTimeout:fn=>fn(), console,
});
// Suppress automatic startup and use a counted renderer to test polling state transitions.
vm.runInContext(source.replace('refresh();setInterval(refresh,3000);',''),context);
context.countRender=()=>renders++;
vm.runInContext('render=()=>{countRender();renderedState=state}',context);
(async()=>{
 await vm.runInContext('refresh()',context);
 assert.equal(renders,0);
 editing=false;
 await vm.runInContext('refresh()',context);
 assert.equal(renders,1,'a skipped update must render even if server state is unchanged');
 await vm.runInContext('refresh()',context);
 assert.equal(renders,1,'unchanged state should not redraw');
 editing=true;next={directory:'/second',sheets:[]};
 await vm.runInContext('refresh()',context);
 editing=false;listeners.focusout();
 assert.equal(renders,2,'blur must flush a deferred update');
 context.fetch=async()=>({ok:false,json:async()=>({error:'failed'})});
 vm.runInContext('toast=()=>{}',context);
 assert.equal(await vm.runInContext("action('config',{})",context),false,'failed save must not clear entered API key');
 console.log('PASS: deferred polling, blur refresh, unchanged state, failed config save');
})().catch(e=>{console.error(e);process.exitCode=1});
