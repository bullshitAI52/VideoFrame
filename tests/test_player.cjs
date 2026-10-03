const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({});vm.runInContext(fs.readFileSync('static/player.js','utf8'),context);
assert.equal(vm.runInContext('playbackStart(2)',context),0);
assert.equal(vm.runInContext('playbackStart(16)',context),11);
assert.equal(vm.runInContext('frameAt(150,75,400,200,16)',context),5);
assert.equal(vm.runInContext('frameAt(350,175,400,200,7)',context),null);
assert.equal(vm.runInContext('frameAt(400,100,400,200,16)',context),null);
assert.equal(vm.runInContext('videoTime(3671)',context),'01:01:11');
console.log('PASS: frame hit testing, empty cells, five-second pre-roll, timestamp formatting');
