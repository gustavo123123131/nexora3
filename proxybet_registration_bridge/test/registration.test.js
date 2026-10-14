'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const os=require('node:os'), fs=require('node:fs'),path=require('node:path');
const {createSiteRegistry,normalizeUrl,validateFields,generatePassword,AccountStore,RegistrationService,AccountLauncher,AuthorizedApiClient,createLogger}=require('../src');
function setup({found={username:true,email:true,password:true,confirmPassword:true}, outcome={status:'completed',stage:'provider-confirmed'}}={}) {
  const fields={ username:{selector:'#user',required:true}, email:{selector:'#email',required:true}, password:{selector:'#pw',required:true}, confirmPassword:{selector:'#again',required:true}};
  const sites=createSiteRegistry([{id:'teste',authorized:true,baseUrl:'https://example.com',registrationPath:'/register',loginPath:'/login',fields,submitSelector:'#send'}]);
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'accounts-test-'));
  const store=new AccountStore(path.join(dir,'accounts.json'));
  const calls=[];
  const browser={
    async openProfile(args){calls.push(['openProfile',args]);return 'browser-existing-profile';},
    async navigate(args){calls.push(['navigate',args]);},
    async inspectFields(){return found;},
    async fillFields(args){calls.push(['fillFields',args]);},
    async submitForm(args){calls.push(['submitForm',args]);},
    async waitForRegistrationOutcome(){return outcome;},
  };
  const logs=[];
  const reg=new RegistrationService({sites,store,browser,log:createLogger(entry=>logs.push(entry))});
  return {sites,store,browser,reg,calls,logs,dir};
}
const values={username:'tester_1',email:'tester@example.com',password:'Really-Strong-2026!',confirmPassword:'Really-Strong-2026!'};
test('normaliza HTTPS e recusa outros protocolos / URLs com credenciais',()=>{
  assert.equal(normalizeUrl('example.com/register').href,'https://example.com/register');
  assert.throws(()=>normalizeUrl('javascript:alert(1)'));
  assert.throws(()=>normalizeUrl('https://login:pass@example.com'));
  assert.throws(()=>normalizeUrl('http://example.com'));
});
test('não ativa site sem permissão explícita',()=>{
  assert.throws(()=>createSiteRegistry([{id:'teste',baseUrl:'https://example.com',fields:{username:{selector:'#x'}},submitSelector:'#send'}]));
});
test('validação recusa senha ruim e confirmação divergente',()=>{
  assert.equal(validateFields({password:'short'}, {password:{required:true}}).ok,false);
  assert.equal(validateFields({password:'Really-Strong-2026!',confirmPassword:'different'}, {password:{required:true},confirmPassword:{required:true}}).ok,false);
  const pass=generatePassword();assert.ok(pass.length>=12);assert.match(pass,/[A-Z]/);assert.match(pass,/[!@#$%*_-]/);
});
test('fluxo prepara, exige revisão e registra somente sucesso confirmado',async(t)=>{
  const x=setup();t.after(()=>fs.rmSync(x.dir,{recursive:true,force:true}));
  const {account,fields}=await x.reg.prepare({siteId:'teste',profileId:'perfil-01',loginIdentifier:values.username});
  assert.equal(account.status,'pending');assert.equal(fields.length,4);
  await assert.rejects(x.reg.submit({accountId:account.id,values}),/Confirmação/);
  assert.equal(x.calls.some(c=>c[0]==='submitForm'),false);
  const result=await x.reg.submit({accountId:account.id,values,userConfirmed:true});
  assert.equal(result.account.status,'completed');assert.equal(x.calls.filter(c=>c[0]==='submitForm').length,1);
  const disk=fs.readFileSync(path.join(x.dir,'accounts.json'),'utf8');
  assert.ok(!disk.includes(values.password));assert.ok(!JSON.stringify(x.logs).includes(values.password));
  assert.equal((fs.statSync(path.join(x.dir,'accounts.json')).mode & 0o777),0o600);
});
test('aguardar verificação não significa cadastro concluído',async(t)=>{
  const x=setup({outcome:{status:'pending',stage:'sms-verification'}});t.after(()=>fs.rmSync(x.dir,{recursive:true,force:true}));
  const {account}=await x.reg.prepare({siteId:'teste',profileId:'P2'});
  const outcome=await x.reg.submit({accountId:account.id,values,userConfirmed:true});
  assert.equal(outcome.account.status,'pending');assert.equal(outcome.account.stage,'sms-verification');
});
test('campo obrigatório ausente bloqueia envio',async(t)=>{
  const x=setup({found:{username:true,password:true,email:false,confirmPassword:true}});t.after(()=>fs.rmSync(x.dir,{recursive:true,force:true}));
  const {account,missingRequired}=await x.reg.prepare({siteId:'teste',profileId:'P2'});
  assert.deepEqual(missingRequired,['email']);
  const result=await x.reg.submit({accountId:account.id,values,userConfirmed:true});
  assert.equal(result.account.status,'failed');assert.equal(x.calls.some(c=>c[0]==='submitForm'),false);
});
test('reabre perfil original usando loginUrl sem copiar cookies nem senhas',async(t)=>{
  const x=setup();t.after(()=>fs.rmSync(x.dir,{recursive:true,force:true}));
  const account=x.store.create({siteId:'teste',profileId:'perfil-original',loginIdentifier:'tester_1'});
  const result=await new AccountLauncher({sites:x.sites,store:x.store,browser:x.browser}).open(account.id);
  assert.equal(result.loginUrl,'https://example.com/login');
  assert.equal(x.calls[0][1].profileId,'perfil-original');
  assert.equal(result.requiresUserAuthentication,true);
});
test('API exige configuração explicitamente autorizada e restringe operações',async()=>{
  const sites=createSiteRegistry([{id:'teste',baseUrl:'https://example.com',authorized:true,fields:{email:{selector:'#x',required:true}},submitSelector:'#send',officialApi:{authorized:true,baseUrl:'https://api.example.com',operations:[{name:'status',method:'GET',path:'/status'}]}}]);
  const calls=[];const api=new AuthorizedApiClient({site:sites.get('teste'),fetchImpl:async(url,opts)=>{calls.push([url,opts]);return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({status:'pending'})};}});
  await assert.rejects(api.call('register',{body:{}}),/não autorizada/);
  const result=await api.call('status');assert.equal(result.status,'pending');assert.equal(calls[0][0],'https://api.example.com/status');
});
test('arquivo não recebe propriedades arbitrárias/segredos',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'accounts-security-'));
 try {const store=new AccountStore(path.join(dir,'db.json'));const a=store.create({siteId:'x',profileId:'y'});
 store.update(a.id,{password:'secret',token:'test123',cookie:'abc'});
 assert.ok(!fs.readFileSync(path.join(dir,'db.json'),'utf8').includes('secret'));
 } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
test('resposta de navegador incerta permanece pendente e não vira sucesso automático',async(t)=>{
  const x=setup({outcome:{foo:'bar'}});t.after(()=>fs.rmSync(x.dir,{recursive:true,force:true}));
  const {account}=await x.reg.prepare({siteId:'teste',profileId:'P3'});
  const res=await x.reg.submit({accountId:account.id,values,userConfirmed:true});
  assert.equal(res.account.status,'pending');
  assert.equal(res.account.stage,'verification-required');
});
test('registro pode ser encontrado e filtrado por status', (t)=>{
  const x=setup();t.after(()=>fs.rmSync(x.dir,{recursive:true,force:true}));
  const account=x.store.create({siteId:'teste',profileId:'perfil-alpha',loginIdentifier:'teste@exemplo.com'});
  x.store.update(account.id,{status:'completed'});
  assert.equal(x.store.list({query:'ALPHA',status:'completed'}).length,1);
  assert.equal(x.store.list({status:'failed'}).length,0);
});
