'use strict';
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const VALID_STATUSES=new Set(['pending','completed','failed']);
function scrubNotes(value='') {
  if(typeof value!=='string')return '';
  return value.slice(0,400).replace(/(?:Bearer\s+\S+|(?:token|password|senha|secret|cookie|api[_-]?key)\s*[:=]\s*\S+)/gi,'[redacted]');
}
class AccountStore {
  constructor(filePath) {
    if(typeof filePath!=='string'||!path.isAbsolute(filePath))throw new Error('Caminho absoluto obrigatório');
    this.filePath=filePath;
    fs.mkdirSync(path.dirname(filePath),{recursive:true,mode:0o700});
  }
  _read() {
    if(!fs.existsSync(this.filePath)) return [];
    const data=JSON.parse(fs.readFileSync(this.filePath,'utf8'));
    if(!Array.isArray(data))throw new Error('Arquivo de contas inválido');
    return data;
  }
  _write(items) {
    const temp=this.filePath+'.'+crypto.randomUUID()+'.tmp';
    try {
      fs.writeFileSync(temp,JSON.stringify(items,null,2),{mode:0o600,flag:'wx'});
      fs.renameSync(temp,this.filePath);
      try{fs.chmodSync(this.filePath,0o600);}catch{}
    } finally {if(fs.existsSync(temp)) fs.unlinkSync(temp);}
  }
  list({query='',status}={}) {
    const q=String(query).toLowerCase().slice(0,128);
    return this._read().filter(x => (!status||x.status===status) && (!q||[x.siteId,x.loginIdentifier,x.profileId].some(s=>String(s||'').toLowerCase().includes(q))));
  }
  get(id){return this._read().find(x=>x.id===id)||null;}
  create({siteId,profileId,loginIdentifier='',notes=''}) {
    if(!siteId||!profileId||typeof siteId!=='string'||typeof profileId!=='string')throw new Error('Site e perfil obrigatórios');
    const now=new Date().toISOString();
    const account={id:crypto.randomUUID(),siteId:siteId.slice(0,64),profileId:profileId.slice(0,128),loginIdentifier:String(loginIdentifier).slice(0,256),status:'pending',stage:'review',notes:scrubNotes(notes),createdAt:now,updatedAt:now};
    const entries=this._read(); entries.push(account);this._write(entries);return account;
  }
  update(id,patch={}){
    const entries=this._read();const account=entries.find(x=>x.id===id);
    if(!account)throw new Error('Conta não encontrada');
    if(patch.status && !VALID_STATUSES.has(patch.status))throw new Error('Status inválido');
    // Campos enumerados explicitamente: impede persistir senha, token, cookie ou payloads completos.
    if(patch.status)account.status=patch.status;
    if(typeof patch.stage==='string')account.stage=patch.stage.slice(0,60);
    if(typeof patch.notes==='string')account.notes=scrubNotes(patch.notes);
    if(typeof patch.loginIdentifier==='string')account.loginIdentifier=patch.loginIdentifier.slice(0,256);
    account.updatedAt=new Date().toISOString(); this._write(entries);return account;
  }
}
module.exports={AccountStore};
