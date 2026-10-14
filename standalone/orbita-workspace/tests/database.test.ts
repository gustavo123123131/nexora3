import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Repository } from '../electron/database';

test('migração, persistência SQLite e partições isoladas', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'orbita-phase1-'));
  const file = join(dir,'state.sqlite');
  try {
    let repo = await Repository.open(file);
    const first = repo.createProfile({label:'Primeiro',homeUrl:'example.com'});
    const second = repo.createProfile({label:'Segundo',homeUrl:'https://example.com/'});
    assert.equal(first.homeUrl,'https://example.com/');
    assert.notEqual(first.storagePartition, second.storagePartition);
    assert.equal(first.storagePartition, 'persist:orbita-' + first.id);
    const parent = repo.createAccount({provider:'Exemplo',displayName:'Mãe',profileId:first.id});
    const child = repo.createAccount({provider:'Exemplo',displayName:'Filha',parentAccountId:parent.id,profileId:second.id});
    assert.equal(repo.listAccounts().length,2);
    assert.equal(repo.listAudit().length,4);
    assert.throws(()=>repo.updateAccount({id:parent.id,parentAccountId:child.id}),/circular/);
    repo.close();
    repo=await Repository.open(file);
    assert.equal(repo.listProfiles().length,2);
    assert.equal(repo.account(child.id).parentAccountId,parent.id);
    assert.equal(repo.profile(first.id).storagePartition,first.storagePartition);
    repo.removeAccount(parent.id);
    assert.equal(repo.account(child.id).parentAccountId,null);
    repo.removeProfile(second.id);
    assert.equal(repo.account(child.id).profileId,null);
    repo.close();
  } finally { rmSync(dir,{recursive:true,force:true}); }
});
test('importação é transacional e nunca salva senhas', async () => {
  const dir=mkdtempSync(join(tmpdir(),'orbita-import-'));
  try {
    const repo=await Repository.open(join(dir,'data.sqlite'));
    assert.throws(()=>repo.importAccounts([
      {provider:'Site',displayName:'Correta'},
      {provider:'',displayName:'Inválida'},
    ]));
    assert.equal(repo.listAccounts().length,0);
    assert.equal(repo.importAccounts([{provider:'Site',displayName:'Local',password:'não-armazenar'}]),1);
    assert.equal(repo.listAccounts().length,1);
    assert.equal(JSON.stringify(repo.listAccounts()).includes('não-armazenar'),false);
    assert.equal(repo.listAudit().at(0)?.action,'account.import');
    repo.close();
  } finally { rmSync(dir,{recursive:true,force:true}); }
});
